# Edge Cases & Design Decisions - PBX Microservice Assessment

## 1. Packet Ingestion: REST POST vs WebSocket

### Decision: Use REST POST for Packet Ingestion

**Rationale:**
- ✅ **Task Specification**: Explicitly requires `POST /v1/call/stream/{call_id}` endpoint
- ✅ **Simplicity**: Stateless, easier to implement and test
- ✅ **Reliability**: Each packet is independently acknowledged (no connection state to manage)
- ✅ **Retry Logic**: PBX can easily retry failed packets without reconnection overhead
- ✅ **Load Balancing**: Easier to distribute across multiple instances (no sticky sessions)
- ✅ **Debugging**: Each request is isolated and traceable

**When WebSocket IS Appropriate:**
- ✅ **Supervisor Dashboard**: Real-time bidirectional updates (`WebSocket /ws/supervisor`)
- ✅ **Broadcast Events**: One update → multiple supervisors efficiently
- ✅ **Low Latency**: Persistent connection eliminates handshake overhead for updates

**Conclusion:** REST POST for ingestion (one-way, stateless), WebSocket for dashboard (bidirectional, real-time).

---

## 2. Grace Period for Late Packets

### Problem
Packets can arrive late due to network delays. If we immediately trigger AI processing when receiving the completion signal, we might miss packets that are "in flight."

### Solution: 3-Second Grace Period

**Implementation:**
```python
async def complete_call(call_id: str, total_packets: int):
    # 1. Transition to COMPLETED
    await update_call_state(call_id, "COMPLETED")
    await set_expected_total_packets(call_id, total_packets)
    
    # 2. Wait for late packets (grace period)
    await asyncio.sleep(3.0)  # 3-second grace period
    
    # 3. Check for missing packets
    call = await get_call(call_id)
    if call.missing_sequences:
        logger.warning(
            f"Call {call_id}: Processing with missing packets. "
            f"Expected: {total_packets}, Received: {call.total_packets_received}, "
            f"Missing: {call.missing_sequences}"
        )
    
    # 4. Transition to PROCESSING_AI
    await update_call_state(call_id, "PROCESSING_AI")
    await trigger_ai_processing(call_id)
```

**Benefits:**
- Gives late packets a chance to arrive
- Reduces incomplete transcriptions
- Still processes within reasonable time (3s is acceptable latency)

**Edge Case: Packet Arrives After Grace Period**
- Packet arrives after call is in `PROCESSING_AI` or `ARCHIVED` state
- **Behavior**: Accept packet, store in database, log warning, but don't reprocess
- **Rationale**: Idempotent behavior, data preserved for audit, no duplicate AI calls

---

## 3. Call Completion with Missing Packets

### Scenario
PBX sends completion signal with `total_packets: 100`, but we only received 95 packets.

### Behavior

**Step-by-Step:**
1. **Receive completion signal**: `POST /v1/call/complete/{call_id}` with `total_packets: 100`
2. **Update expected count**: Store `expected_total_packets = 100`
3. **Transition to COMPLETED**: `IN_PROGRESS → COMPLETED`
4. **Grace period**: Wait 3 seconds for late packets
5. **Check completeness**:
   ```python
   if call.total_packets_received < call.expected_total_packets:
       missing_count = call.expected_total_packets - call.total_packets_received
       logger.warning(
           f"Call {call_id}: Incomplete. "
           f"Expected: {call.expected_total_packets}, "
           f"Received: {call.total_packets_received}, "
           f"Missing sequences: {call.missing_sequences}"
       )
   ```
6. **Process anyway**: Transition to `PROCESSING_AI` and send available packets to AI
7. **AI processes best-effort**: Transcription may have gaps, but partial result is better than none

**Documented Assumption:**
> "The system prioritizes delivering results over perfect completeness. If packets are missing after the grace period, AI processing proceeds with available data. Missing packets are logged for audit and debugging."

---

## 4. WebSocket Supervisor Connections

### Scope for Assessment
- **Expected connections**: 1-5 concurrent supervisors
- **Sufficient for**: Demonstration and testing purposes

### Implementation
```python
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    async def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
    
    async def broadcast(self, message: dict):
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        
        # Clean up dead connections
        for conn in dead_connections:
            self.active_connections.remove(conn)
```

**Production Enhancements (Out of Scope):**
- Connection limits and rate limiting
- Heartbeat/ping-pong for dead connection detection
- Selective subscriptions (supervisors filter by call_id)
- Redis pub/sub for multi-instance broadcasting

---

## 5. AI Service Data Format

### Problem
The task doesn't specify the format for sending audio data to the AI service.

### Solution: Simple Concatenated String (Assessment Simplification)

**Rationale:**
- ✅ **Simplicity**: Easy to implement and test
- ✅ **Realistic**: Simulates sending "audio transcript" or "audio metadata"
- ✅ **Sufficient**: Demonstrates the retry logic and state management (core assessment focus)

**Implementation:**
```python
async def prepare_audio_for_ai(call_id: str) -> str:
    # Fetch all packets ordered by sequence
    packets = await db.execute(
        select(Packet)
        .where(Packet.call_id == call_id)
        .order_by(Packet.sequence)
    )
    
    # Concatenate packet data with delimiter
    audio_data = " ".join([p.data for p in packets.scalars()])
    return audio_data

# Example result:
# "audio_chunk_1 audio_chunk_2 audio_chunk_3 ..."
```

**Mock AI Service Expectation:**
```python
async def transcribe(audio_data: str) -> dict:
    # Expects: concatenated string
    # Returns: transcription + sentiment
    return {
        "transcription": f"Transcribed: {audio_data[:50]}...",
        "sentiment": "positive",
        "confidence": 0.87
    }
```

**Production Consideration (Out of Scope):**
- Real AI services might expect: base64-encoded audio, WAV format, JSON array, etc.
- For assessment: string concatenation is sufficient to demonstrate system behavior

---

## 6. State Machine - COMPLETED State with Grace Period

### Updated State Flow

```
IN_PROGRESS → COMPLETED (3s grace period) → PROCESSING_AI → ARCHIVED
                                                         ↓
                                                      FAILED
```

**COMPLETED State Purpose:**
1. Signal that PBX has finished sending packets
2. Store expected total packet count
3. Wait for late packets (3-second grace period)
4. Validate completeness before AI processing

**State Duration:**
- **Minimum**: 3 seconds (grace period)
- **Maximum**: 3 seconds (no additional waiting)

**Why This Works:**
- Clear separation between "call ended" and "ready for AI"
- Gives network time to deliver late packets
- Prevents premature AI processing
- Still maintains acceptable latency (3s is reasonable)

---

## 7. Testing - Race Condition Simulation

### Approach: `asyncio.gather()` for Concurrent Requests

**Test Implementation:**
```python
import pytest
import httpx
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_race_condition_concurrent_packets():
    """
    Test: Two packets for the same call_id arrive simultaneously.
    Expected: Both packets stored, no lost updates, correct packet count.
    """
    async with AsyncClient(app=app, base_url="http://test") as client:
        call_id = "race_test_123"
        
        # Create call first
        await client.post(f"/v1/call/stream/{call_id}", json={
            "sequence": 0,
            "data": "initial",
            "timestamp": 1234567890.0
        })
        
        # Send two packets concurrently (same call_id, different sequences)
        packet_1 = {
            "sequence": 1,
            "data": "packet_one",
            "timestamp": 1234567891.0
        }
        packet_2 = {
            "sequence": 2,
            "data": "packet_two",
            "timestamp": 1234567892.0
        }
        
        # Execute concurrently using asyncio.gather
        responses = await asyncio.gather(
            client.post(f"/v1/call/stream/{call_id}", json=packet_1),
            client.post(f"/v1/call/stream/{call_id}", json=packet_2)
        )
        
        # Both should return 202 Accepted
        assert all(r.status_code == 202 for r in responses)
        
        # Wait for background tasks to complete
        await asyncio.sleep(0.5)
        
        # Verify both packets stored
        call = await get_call(call_id)
        assert call.total_packets_received == 3  # 0, 1, 2
        
        packets = await get_packets(call_id)
        assert len(packets) == 3
        assert {p.sequence for p in packets} == {0, 1, 2}
```

**Why `asyncio.gather()`:**
- ✅ **True Concurrency**: Both requests execute in parallel
- ✅ **Async-Native**: Works with FastAPI's async architecture
- ✅ **Deterministic**: Reproducible test results
- ✅ **Simple**: No threading complexity

**Alternative Approaches (Not Chosen):**
- ❌ **Threading**: More complex, harder to debug, overkill for async tests
- ❌ **Multiple clients**: Adds overhead, doesn't guarantee simultaneity

---

## 8. Failed Calls - Retry Exhaustion Handling

### Scenario
AI service fails after 5 retry attempts (exponential backoff exhausted).

### Behavior

**State Transition:**
```
PROCESSING_AI → FAILED (after max retries)
```

**Database Storage:**
```python
# Update call record
call.state = "FAILED"
call.failure_reason = "AI service unavailable after 5 retries"
call.failed_at = datetime.utcnow()
await db.commit()

# Log for monitoring
logger.error(
    f"Call {call_id} FAILED: AI processing exhausted retries. "
    f"Attempts: 5, Last error: 503 Service Unavailable"
)
```

**What Happens Next (Assessment Scope):**
- ✅ Call remains in `FAILED` state in database
- ✅ Supervisor dashboard shows failure event via WebSocket
- ✅ Available for manual review/debugging
- ✅ Can be queried via API (future endpoint: `GET /v1/calls/{call_id}`)

**Production Enhancements (Out of Scope):**
- Dead letter queue for manual intervention
- Automatic retry after X hours
- Alert/notification to operations team
- Bulk retry endpoint for failed calls
- Fallback to alternative AI service

**WebSocket Failure Event:**
```json
{
  "event": "call_failed",
  "call_id": "123",
  "state": "FAILED",
  "reason": "AI service unavailable after 5 retries",
  "timestamp": 1234567890.123
}
```

---

## 9. Additional Edge Cases

### 9.1 Duplicate Packets During Grace Period

**Scenario:** Packet 50 arrives, then duplicate arrives during grace period.

**Behavior:**
- First arrival: Stored successfully
- Duplicate: UNIQUE constraint violation → Return 202 with `duplicate: true`
- No impact on grace period or AI processing

---

### 9.2 Call Completion Called Twice

**Scenario:** PBX accidentally sends completion signal twice.

**Behavior:**
```python
# First call: IN_PROGRESS → COMPLETED (succeeds)
# Second call: COMPLETED → COMPLETED (no-op, already completed)

if current_state == "COMPLETED":
    logger.warning(f"Call {call_id} already completed, ignoring duplicate signal")
    return {"status": "already_completed"}
```

---

### 9.3 Packets Arrive After Call Marked FAILED

**Scenario:** Call fails AI processing, then late packets arrive.

**Behavior:**
- Accept and store packets (idempotent)
- Log warning: "Packet received for FAILED call"
- Don't re-trigger AI processing
- Data preserved for debugging

---

### 9.4 Out-of-Order Packets Beyond Missing Array Limit

**Scenario:** More than 100 missing sequences detected.

**Behavior:**
```python
if len(missing_sequences) >= 100:
    logger.error(
        f"Call {call_id}: Missing sequences limit exceeded. "
        f"Call likely corrupted. Current missing: {len(missing_sequences)}"
    )
    # Still accept packets, but don't grow array further
    # This prevents unbounded memory growth
```

---

## 10. Summary of Key Decisions

| Edge Case | Decision | Rationale |
|-----------|----------|-----------|
| **Ingestion Method** | REST POST | Task requirement, simplicity, stateless |
| **Late Packets** | 3-second grace period | Balance between completeness and latency |
| **Missing Packets** | Process anyway | Partial results > no results |
| **Duplicate Packets** | Idempotent 202 response | Safe retry behavior, UNIQUE constraint |
| **AI Data Format** | Concatenated string | Assessment simplification, demonstrates core logic |
| **Failed Calls** | Store in DB as FAILED | Audit trail, manual review capability |
| **Race Conditions** | Row-level locking | Correct, simple, sufficient for scale |
| **WebSocket Scale** | 1-5 connections | Assessment scope, easy to extend |
| **Grace Period State** | COMPLETED (3s) | Clear separation, late packet window |
| **Test Concurrency** | `asyncio.gather()` | Async-native, deterministic, simple |

---
