## 1. Core Problem Statement

### What is Being Built?
A real-time microservice that acts as an intermediary between a PBX (Private Branch Exchange) telephone system and an AI-powered voice bot. The system must handle streaming audio metadata, coordinate AI processing, and provide real-time visibility to supervisors.

### The Real-World Context
Imagine a call center where customer calls are routed to an AI assistant. The PBX system breaks down the audio into small chunks (packets) and sends them continuously to our microservice. Our job is to:
1. Receive these packets reliably
2. Detect if any packets are lost or arrive out of order
3. Send the complete audio to an AI service for transcription and sentiment analysis
4. Store everything for supervisor review
5. Handle the fact that the AI service is unreliable (fails 25% of the time)

---

## 2. Key Technical Challenges

### Challenge 1: Non-Blocking Packet Ingestion

**The Problem:**
When a packet arrives, we must acknowledge it within 50 milliseconds. If we take longer, the PBX might think we're down and stop sending packets.

**Why It's Hard:**
Storing data in a database, logging, and broadcasting to supervisors all take time. If we do these synchronously, we'll exceed 50ms.

**The Concept:**
We need to separate "acknowledgment" from "processing." Think of it like a restaurant:
- The host (API) immediately seats you and says "we got you" (<50ms)
- The kitchen (background tasks) actually prepares your food (slower, but doesn't block new customers)

**Key Insight:**
Asynchronous processing is essential. The API endpoint must return immediately while queuing work for later.

---

### Challenge 2: Packet Sequence Validation

**The Problem:**
Network packets can arrive out of order, get lost, or arrive twice. We need to detect all these scenarios.

**Real-World Analogy:**
Imagine receiving pages of a book through the mail:
- **In-order**: Pages 1, 2, 3, 4, 5 arrive sequentially ✓
- **Gap**: Pages 1, 2, 3, 5 arrive (where's page 4?) ⚠️
- **Late arrival**: Page 5 arrives, then page 4 shows up later ⚠️
- **Duplicate**: Page 4 arrives twice ❌

**The Concept:**
We maintain three pieces of state:
1. **Last sequence received**: The highest sequence number we've seen
2. **Expected next sequence**: What we're waiting for next
3. **Missing sequences**: A list of gaps we've detected

**Why the missing_sequences Array is Critical:**
This array is the key to distinguishing between:
- **Late arrival**: "Oh, this is packet 4 that we were missing!" → Accept it
- **True duplicate**: "We already have packet 4!" → Reject it

Without this array, we couldn't tell the difference between a late packet and a duplicate.

---

### Challenge 3: Race Conditions

**The Problem:**
Two packets for the same call might arrive at the exact same moment on different threads. Both try to update the packet count. Without proper handling, one update gets lost.

**Real-World Analogy:**
Two bank tellers simultaneously processing deposits to the same account:
- Both read balance: $100
- Both add $50
- Both write: $150
- **Expected result**: $200
- **Actual result**: $150 (one deposit lost!)

**The Concept: Row-Level Locking**
When Thread 1 starts updating a call, it "locks" that specific database row. Thread 2 must wait until Thread 1 finishes. This ensures serial execution for the same call.

**Why This Doesn't Hurt Performance:**
Different calls lock different rows. So:
- Thread 1 updating call_12345 (locks row A)
- Thread 2 updating call_67890 (locks row B)
- Both proceed in parallel! ✓

**Key Insight:**
Row-level locking provides:
- **Safety**: No lost updates for the same call
- **Performance**: Different calls process in parallel

#### Additional Race Condition Scenarios

Beyond the basic packet count race, here are other critical race conditions in our system:

**Race Condition 1: State Transition Race**

*Scenario:*
- Thread 1: Call ends, tries to transition IN_PROGRESS → COMPLETED
- Thread 2: Last packet arrives, also tries to transition IN_PROGRESS → COMPLETED
- Both check current state = IN_PROGRESS
- Both try to update to COMPLETED

*Problem:*
- Might log transition twice
- Might trigger AI processing twice
- Audit trail shows duplicate transitions

*Solution:*
- Row-level locking on state updates
- Atomic compare-and-swap operation ensures only one transition succeeds

---

**Race Condition 2: Missing Sequences Array Update**

*Scenario:*
- Thread 1: Packet 5 arrives, detects gap [4], tries to add to missing_sequences
- Thread 2: Packet 7 arrives, detects gap [6], tries to add to missing_sequences
- Both read: missing_sequences = []
- Thread 1 writes: [4]
- Thread 2 writes: [6] (overwrites Thread 1!)
- **Expected**: [4, 6]
- **Actual**: [6] (lost [4]!)

*Problem:*
- Lost track of missing packet 4
- Can't detect when packet 4 arrives late
- Might accept duplicate packet 4 as "late arrival"

*Solution:*
- Row-level locking before array updates
- Or use database array append operation (atomic)

---

**Race Condition 3: Duplicate Detection**

*Scenario:*
- Thread 1: Packet 4 arrives (late), checks if exists in database
- Thread 2: Packet 4 arrives (duplicate), checks if exists in database
- Both query: "Does packet 4 exist for this call?"
- Both get: No (doesn't exist yet)
- Both decide: "Not a duplicate, store it!"
- Both try to insert packet 4

*Problem:*
- Packet stored twice
- Database constraint violation (if UNIQUE constraint exists)
- Or silent duplicate (if no constraint)

*Solution:*
- UNIQUE constraint on (call_id, sequence) - database rejects second insert
- Row-level locking before duplicate check

---

**Race Condition 4: AI Processing Trigger**

*Scenario:*
- Thread 1: Packet 99 arrives (second-to-last), checks if call complete
- Thread 2: Packet 100 arrives (last), checks if call complete
- Both see: 99 packets received, 100 expected
- Thread 1: Not complete yet
- Thread 2: Complete! Trigger AI processing
- Thread 1: Now sees 100 packets, also triggers AI processing

*Problem:*
- AI service called twice for same call
- Wasted resources and API costs
- Potential duplicate results in database

*Solution:*
- State machine prevents this: Can only transition COMPLETED → PROCESSING_AI once
- Row-level locking ensures atomic state check and transition

---

**Race Condition 5: Total Packet Count**

*Scenario:*
- Thread 1: Packet 4 arrives late, increments total_packets_received
- Thread 2: Packet 5 arrives, increments total_packets_received
- Both read: total_packets_received = 3
- Both write: total_packets_received = 4
- **Expected**: 5
- **Actual**: 4 (lost update!)

*Problem:*
- Incorrect metrics and statistics
- Can't trust packet counts for completeness checks
- Supervisor dashboard shows wrong numbers

*Solution:*
- Row-level locking (our chosen solution)
- Or atomic increment operation

---

**When Do Race Conditions Occur?**

Race conditions appear whenever:
1. **Multiple threads** access shared state
2. **At least one thread** modifies the state
3. **No synchronization** mechanism exists

**Our Defense Strategy:**
- **Primary**: Row-level database locking
- **Backup**: UNIQUE constraints on critical fields
- **Validation**: State machine prevents invalid transitions
- **Atomic Operations**: Use database atomic operations where possible

---

### Challenge 4: Unreliable AI Service

**The Problem:**
The external AI service fails 25% of the time (returns 503 Service Unavailable). We can't just give up; we need to retry intelligently.

**Why Simple Retry Fails:**
If we retry immediately every time:
- The service might be temporarily overloaded
- We make the problem worse by hammering it
- We waste resources on rapid failures

**The Concept: Exponential Backoff**
Wait progressively longer between retries:
- Attempt 1: Immediate
- Attempt 2: Wait 1 second
- Attempt 3: Wait 2 seconds
- Attempt 4: Wait 4 seconds
- Attempt 5: Wait 8 seconds

**Why This Works:**
- Gives the service time to recover
- Reduces load on the failing service
- Balances persistence with resource efficiency

**Real-World Analogy:**
If a restaurant is full, you don't check every 10 seconds. You wait 15 minutes, then 30 minutes, then an hour before checking again.

---

### Challenge 5: State Machine Management

**The Problem:**
A call goes through multiple states, and we need to ensure valid transitions. We can't jump from "in progress" directly to "archived" without completing intermediate steps.

**The States:**
1. **IN_PROGRESS**: Call is active, packets streaming in
2. **COMPLETED**: All packets received, call ended
3. **PROCESSING_AI**: Sending to AI for transcription
4. **ARCHIVED**: Successfully processed and stored
5. **FAILED**: AI processing failed after all retries

**Valid Transitions:**
- IN_PROGRESS → COMPLETED (call ends)
- COMPLETED → PROCESSING_AI (start AI processing)
- PROCESSING_AI → ARCHIVED (AI succeeds)
- PROCESSING_AI → FAILED (AI fails after retries)

**Invalid Transitions:**
- IN_PROGRESS → ARCHIVED (can't skip steps)
- COMPLETED → FAILED (can't fail without trying AI)
- ARCHIVED → PROCESSING_AI (can't reprocess)

**Why This Matters:**
State machines prevent logical errors and provide clear audit trails. Each state has specific meaning and allowed next steps.

---

## 3. Design Philosophy

### Principle 1: Fail Fast, Recover Gracefully

**Don't Block on Errors:**
- Packet missing? Log it, continue processing
- AI service down? Retry with backoff
- Never let one failure stop the entire system

**Real-World Analogy:**
A restaurant doesn't close because one dish is out of stock. They note it, offer alternatives, and keep serving other customers.

---

### Principle 2: Separation of Concerns

**Each Layer Has One Job:**
- **API Layer**: Receive requests, validate, respond quickly
- **Business Logic**: Apply rules (sequence validation, state transitions)
- **Background Processing**: Slow operations (database, AI calls)
- **Data Layer**: Store and retrieve data

**Why This Matters:**
Clear boundaries make the system:
- Easier to test (test each layer independently)
- Easier to scale (scale only the bottleneck layer)
- Easier to maintain (changes in one layer don't break others)

---

### Principle 3: Optimize for the Common Case

**Common Case (99%):**
- Packets arrive in order
- AI service succeeds
- No race conditions

**Edge Cases (1%):**
- Out-of-order packets
- AI failures
- Concurrent updates

**Design Strategy:**
- Make the common case fast (minimal overhead)
- Handle edge cases correctly (even if slightly slower)

**Example:**
In-order packets: Simple increment, very fast
Out-of-order packets: Check missing array, slightly slower but correct

---

## 4. Why Each Requirement Exists

### Requirement: <50ms Response Time

**Purpose:** Ensure real-time performance for streaming audio

**Real-World Impact:** If we're too slow, the PBX might:
- Buffer packets (increasing latency)
- Drop packets (losing audio)
- Mark us as unhealthy (stop sending)

**How We Achieve It:** Async processing, background tasks, minimal blocking

---

### Requirement: Packet Sequence Validation

**Purpose:** Detect network issues and data integrity problems

**Real-World Impact:** Without validation:
- Missing audio chunks go unnoticed
- Transcription has gaps
- No way to request retransmission

**How We Achieve It:** Sequence number tracking, missing_sequences array

---

### Requirement: Race Condition Handling

**Purpose:** Ensure data consistency under concurrent load

**Real-World Impact:** Without proper handling:
- Packet counts are wrong
- State transitions corrupt
- Audit trails incomplete

**How We Achieve It:** Database row-level locking

---

### Requirement: AI Retry Logic

**Purpose:** Handle unreliable external services gracefully

**Real-World Impact:** Without retries:
- 25% of calls fail permanently
- Poor user experience
- Wasted audio data

**How We Achieve It:** Exponential backoff retry with max attempts

---

### Requirement: WebSocket Communication

**Purpose:** Enable real-time, bidirectional communication for supervisor dashboard

**Usage Clarification:**
- **Packet Ingestion**: Use REST POST endpoint as specified (`POST /v1/call/stream/{call_id}`)
- **Supervisor Dashboard**: Use WebSocket for real-time updates (`WebSocket /ws/supervisor`)

**Why This Split:**
- REST POST is simpler for one-way packet ingestion
- WebSocket is appropriate for bidirectional, real-time dashboard broadcasts
- Task explicitly specifies POST endpoint for ingestion
- "Use websockets wherever appropriate" refers to supervisor updates

**Real-World Impact:** WebSockets provide:
- Lower latency than HTTP polling for dashboard updates
- Instant supervisor visibility into call status
- Efficient resource usage (persistent connection vs repeated polling)
- Broadcast capability (one update → multiple supervisors)

**How We Achieve It:** 
- FastAPI WebSocket endpoint for supervisor connections
- Connection manager to track active supervisors
- Broadcast events: packet received, state changes, AI results

---

## 5. Trade-offs and Decisions

### Trade-off 1: Database Locking vs Message Queue

**Database Locking (Chosen):**
- ✅ Simpler (no extra infrastructure)
- ✅ Directly tests concurrency concepts
- ✅ Sufficient for expected load
- ❌ Doesn't scale to extreme throughput

**Message Queue (Alternative):**
- ✅ Better for high scale (>10k packets/sec)
- ✅ Guaranteed ordering
- ❌ More complex infrastructure
- ❌ Eventual consistency

**Decision:** Use database locking for assessment, mention message queue as production enhancement.

---

### Trade-off 2: Immediate vs Eventual Consistency

**Immediate Consistency (Chosen):**
- Database writes are synchronous
- Once acknowledged, data is persisted
- Stronger guarantees

**Eventual Consistency (Alternative):**
- Queue writes, process later
- Faster acknowledgment
- Risk of data loss if system crashes

**Decision:** Immediate consistency for data integrity, async for non-critical operations (broadcasts).

---

## 6. Success Metrics

### Functional Success
- ✅ All packets processed correctly
- ✅ No lost updates (race conditions prevented)
- ✅ State transitions valid
- ✅ AI failures recovered via retry

### Performance Success
- ✅ API response <50ms
- ✅ Handle 1000+ packets/sec
- ✅ WebSocket latency <100ms

### Code Quality Success
- ✅ Clear separation of concerns
- ✅ Comprehensive error handling
- ✅ Proper logging and monitoring
- ✅ Testable components

---

## 7. Implementation Clarifications

### Call Completion Signal

**The Question:** How do we know when a call is complete?

**The Answer:** Use a separate completion endpoint:
```
POST /v1/call/complete/{call_id}
{
  "total_packets": 100
}
```

**Rationale:**
- Explicit is better than implicit
- Easy to test and verify
- Mirrors real-world PBX behavior (sends end-of-call signal)
- Allows validation: did we receive all expected packets?

**State Flow:**
- Completion endpoint triggers: `IN_PROGRESS → COMPLETED`
- Then immediately: `COMPLETED → PROCESSING_AI` (trigger AI processing)

---

### AI Processing with Missing Packets

**The Decision:** Process AI immediately when call completes, even if packets are missing.

**Why This Makes Sense:**
- **Pragmatic**: 95% accurate transcription > no transcription
- **Resilient**: Don't wait indefinitely for packets that may never arrive
- **Business Value**: Partial results are still useful for supervisors
- **Real-World**: Systems prioritize delivering results over perfect completeness

**What We Do:**
1. Call completion signal received
2. Check if `missing_sequences` is empty
3. If NOT empty: Log warning with missing packet numbers
4. Transition to `PROCESSING_AI` regardless
5. Fetch all available packets, send to AI
6. AI processes incomplete audio (best-effort transcription)

**Alternative Considered:** Wait for all packets with timeout
- **Problem**: Adds complexity, still might be incomplete after timeout
- **Problem**: Delays results for supervisors
- **Problem**: Missing packet might never arrive

---

### Database Schema Design

**Two-Table Approach:**

**Calls Table** (metadata and state):
- `call_id` (primary key)
- `state` (IN_PROGRESS, COMPLETED, PROCESSING_AI, ARCHIVED, FAILED)
- `total_packets_received` (counter)
- `expected_total_packets` (from completion signal)
- `expected_next_sequence` (for gap detection)
- `missing_sequences` (array of missing packet numbers)
- `transcription` (AI result)
- `sentiment` (AI result)
- `created_at`, `updated_at` (timestamps)

**Packets Table** (individual packet data):
- `id` (auto-increment)
- `call_id` (foreign key)
- `sequence` (packet sequence number)
- `data` (packet payload)
- `timestamp` (from packet)
- `received_at` (server timestamp)
- **UNIQUE constraint** on `(call_id, sequence)` - prevents duplicates

**Why This Design:**
- Preserves individual packet integrity
- Supports gap detection and late arrivals
- Can reconstruct full audio on-demand for AI processing
- UNIQUE constraint automatically prevents duplicate storage
- No need to aggregate data in calls table (fetch on-demand)

---

### AI Service Design

**Single Combined Service:**
One AI service that returns both transcription and sentiment analysis.

**Mock Response:**
```json
{
  "transcription": "Customer called about billing issue...",
  "sentiment": "negative",
  "confidence": 0.87
}
```

**Service Characteristics:**
- 25% failure rate (returns 503 Service Unavailable)
- 1-3 second variable latency
- Returns both results on success

**Why Combined:**
- Simpler implementation (one retry logic)
- Realistic (many AI services provide both)
- Easier to test

---

### Duplicate Packet Handling

**The Behavior:** Return 202 Accepted (idempotent), don't store duplicate.

**Response:**
```json
{
  "status": "duplicate",
  "message": "Packet already received",
  "ignored": true
}
```

**Implementation:**
- UNIQUE constraint on `(call_id, sequence)` prevents storage
- Catch `IntegrityError` from database
- Return 202 with duplicate flag
- Log warning for monitoring
- Don't increment packet counters

**Why 202 (not 400):**
- Client did nothing wrong (network can cause duplicates)
- Idempotent behavior (safe to retry)
- System handles it gracefully

---

### Supervisor Dashboard Events

**WebSocket broadcasts all real-time events:**

1. **Packet Received:**
   ```json
   {
     "event": "packet_received",
     "call_id": "123",
     "sequence": 42,
     "total_received": 42,
     "missing_sequences": [15, 23]
   }
   ```

2. **State Changed:**
   ```json
   {
     "event": "state_changed",
     "call_id": "123",
     "from_state": "IN_PROGRESS",
     "to_state": "COMPLETED"
   }
   ```

3. **AI Completed:**
   ```json
   {
     "event": "ai_completed",
     "call_id": "123",
     "transcription": "...",
     "sentiment": "positive"
   }
   ```

**Connection Management:**
- Track active WebSocket connections
- Handle disconnects gracefully (remove from active list)
- Catch send exceptions (indicates dead connection)
- Clean up resources on disconnect

---

## 8. Key Implementation Decisions

### Timeouts and Limits

**AI Processing Timeout:**
- Per-request timeout: 30 seconds
- Cumulative timeout: 60 seconds (across all retries)
- Prevents calls stuck in PROCESSING_AI forever
- After timeout: transition to FAILED state

**Missing Sequences Array:**
- Cap at 100 missing sequences
- If exceeded: log error, still accept packets
- Prevents unbounded array growth
- 100+ missing packets indicates corrupted call anyway

**Concurrent Calls:**
- No explicit limit for assessment
- Rely on database connection pool (natural limit)
- Document assumption for production deployment

### Connection Pooling

**SQLAlchemy handles connection pooling automatically:**
- Default: 5 connections in pool, 10 overflow (total: 15)
- Reuses existing connections (fast)
- Avoids creating new connections per request (slow)
- Sufficient for assessment scale

**For production:**
- Tune pool_size based on load
- Consider external pooler (PgBouncer) for extreme scale
- Monitor connection usage

### State Transition Logging

**Simple Approach (Assessment):**
- Update state field in calls table
- Use application logging for audit trail
- `logger.info(f"Call {call_id}: {from_state} → {to_state}")`

**Production Enhancement:**
- Separate `call_state_history` table
- Track all transitions with timestamps and reasons
- Enables detailed debugging and analytics

---

## 9. Final Assumptions to Document

When submitting the assessment, document these assumptions:

1. **Call Completion:** Explicit completion signal via `POST /v1/call/complete/{call_id}` endpoint

2. **Missing Packets:** Process AI even with gaps (best-effort approach)

3. **Duplicate Handling:** Idempotent (return 202, don't store, log warning)

4. **AI Service:** Combined transcription + sentiment in one service

5. **Packet Storage:** Individual packets stored in separate table for reconstruction

6. **WebSocket Usage:** Used for supervisor dashboard updates, not packet ingestion

7. **Connection Pooling:** SQLAlchemy default settings sufficient for assessment scale

8. **Concurrent Calls:** Limited by database connection pool, no explicit application limit

9. **Timeouts:** 30s per AI request, 60s cumulative across retries

10. **Missing Sequences:** Capped at 100 to prevent unbounded growth

These decisions are defensible and align with real-world system design principles.

---