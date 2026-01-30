# High Level Design - PBX Microservice Assessment

## 1. System Overview

### Purpose
Build a real-time microservice that ingests streaming audio metadata from a PBX system, orchestrates AI processing for transcription and sentiment analysis, and provides real-time visibility to supervisors.

### Key Requirements
- Non-blocking packet ingestion (<50ms response time)
- Handle out-of-order and missing packets
- Manage unreliable AI service (25% failure rate)
- Real-time supervisor dashboard via WebSocket
- State machine for call lifecycle management
- Race condition handling for concurrent requests

---

## 2. System Architecture

```
┌─────────────┐
│  PBX System │
└──────┬──────┘
       │ HTTP POST (packets)
       ▼
┌─────────────────────────────────────────────────────┐
│              FastAPI Microservice                   │
│                                                     │
│  ┌──────────────┐      ┌──────────────┐           │
│  │ REST API     │      │  WebSocket   │           │
│  │ Endpoints    │      │  Endpoint    │           │
│  └──────┬───────┘      └──────┬───────┘           │
│         │                     │                    │
│         ▼                     │                    │
│  ┌──────────────┐             │                    │
│  │ Business     │             │                    │
│  │ Logic Layer  │             │                    │
│  └──────┬───────┘             │                    │
│         │                     │                    │
│         ▼                     │                    │
│  ┌──────────────┐      ┌──────▼───────┐           │
│  │ Background   │      │ Connection   │           │
│  │ Tasks        │──────▶ Manager      │           │
│  └──────┬───────┘      └──────────────┘           │
│         │                                          │
└─────────┼──────────────────────────────────────────┘
          │
          ▼
┌─────────────────────┐
│   PostgreSQL DB     │
│  ┌───────────────┐  │
│  │ Calls Table   │  │
│  │ Packets Table │  │
│  └───────────────┘  │
└─────────────────────┘
          │
          ▼
┌─────────────────────┐
│  Mock AI Service    │
│  (25% failure rate) │
└─────────────────────┘
          │
          ▼
┌─────────────────────┐
│ Supervisor Dashboard│
│   (WebSocket)       │
└─────────────────────┘
```

---

## 3. Component Design

### 3.1 REST API Layer

**Endpoints:**

1. **POST /v1/call/stream/{call_id}**
   - **Purpose**: Ingest audio metadata packets
   - **Input**: `{"sequence": int, "data": str, "timestamp": float}`
   - **Response**: `202 Accepted` (within <50ms)
   - **Behavior**: 
     - Validate input (Pydantic)
     - Queue for background processing
     - Return immediately (non-blocking)

2. **POST /v1/call/complete/{call_id}**
   - **Purpose**: Signal call completion
   - **Input**: `{"total_packets": int}`
   - **Response**: `202 Accepted`
   - **Behavior**:
     - Transition state: IN_PROGRESS → COMPLETED
     - Store expected total packet count
     - Wait 3 seconds (grace period for late packets)
     - Check for missing packets (log warning if incomplete)
     - Transition state: COMPLETED → PROCESSING_AI
     - Trigger AI processing

**Key Features:**
- Async/await for non-blocking I/O
- Pydantic validation for input
- Fast acknowledgment (<50ms)
- Error handling with appropriate HTTP status codes

---

### 3.2 Business Logic Layer

**Responsibilities:**

1. **Sequence Validation**
   - Track expected next sequence
   - Detect gaps (missing packets)
   - Identify late arrivals vs duplicates
   - Maintain `missing_sequences` array

2. **State Machine Management**
   - Enforce valid state transitions
   - Prevent invalid transitions
   - Atomic state updates

3. **Duplicate Detection**
   - Check if packet already exists
   - Return idempotent response
   - Use database UNIQUE constraint

4. **Race Condition Prevention**
   - Row-level database locking
   - Atomic operations
   - Transaction management

**State Machine:**
```
IN_PROGRESS → COMPLETED (3s grace period) → PROCESSING_AI → ARCHIVED
                                                          ↓
                                                       FAILED
```

**Valid Transitions:**
- IN_PROGRESS → COMPLETED (call ends)
- COMPLETED → PROCESSING_AI (start AI)
- PROCESSING_AI → ARCHIVED (AI success)
- PROCESSING_AI → FAILED (AI exhausted retries)

---

### 3.3 Background Processing Layer

**Async Tasks:**

1. **Packet Storage**
   - Store packet in database
   - Update call metadata (counts, sequences)
   - Handle database locking
   - Broadcast to WebSocket clients

2. **AI Processing**
   - Triggered on COMPLETED → PROCESSING_AI transition
   - Fetch all packets for call
   - Concatenate packet data
   - Call AI service with retry logic
   - Store results (transcription, sentiment)
   - Transition to ARCHIVED or FAILED

3. **WebSocket Broadcasting**
   - Broadcast packet received events
   - Broadcast state change events
   - Broadcast AI completion events
   - Handle client disconnections

**Retry Logic (Exponential Backoff):**
```
Attempt 1: Immediate
Attempt 2: Wait 1s
Attempt 3: Wait 2s
Attempt 4: Wait 4s
Attempt 5: Wait 8s
Max cumulative timeout: 60s
```

---

### 3.4 WebSocket Layer

**Endpoint:**
- `WebSocket /ws/supervisor`

**Connection Manager:**
- Track active connections
- Handle connect/disconnect
- Broadcast to all connected clients
- Clean up dead connections

**Event Types:**
1. **packet_received**: New packet ingested
2. **state_changed**: Call state transition
3. **ai_completed**: AI processing finished

**Message Format:**
```json
{
  "event": "packet_received",
  "call_id": "123",
  "sequence": 42,
  "total_received": 42,
  "missing_sequences": [15, 23],
  "timestamp": 1234567890.123
}
```

---

### 3.5 Data Layer

**Database: PostgreSQL with Async Engine (SQLAlchemy)**

**Schema:**

**Calls Table:**
```sql
CREATE TABLE calls (
    call_id VARCHAR PRIMARY KEY,
    state VARCHAR NOT NULL,
    total_packets_received INTEGER DEFAULT 0,
    expected_total_packets INTEGER,
    expected_next_sequence INTEGER DEFAULT 0,
    missing_sequences INTEGER[],
    transcription TEXT,
    sentiment VARCHAR,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

**Packets Table:**
```sql
CREATE TABLE packets (
    id SERIAL PRIMARY KEY,
    call_id VARCHAR REFERENCES calls(call_id),
    sequence INTEGER NOT NULL,
    data TEXT NOT NULL,
    timestamp FLOAT NOT NULL,
    received_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(call_id, sequence)  -- Prevent duplicates
);

CREATE INDEX idx_packets_call_id ON packets(call_id);
CREATE INDEX idx_packets_sequence ON packets(call_id, sequence);
```

**Connection Pooling:**
- SQLAlchemy default: 5 connections + 10 overflow = 15 total
- Sufficient for assessment scale

---

### 3.6 Mock AI Service

**Characteristics:**
- 25% failure rate (returns 503)
- 1-3 second variable latency
- Returns combined transcription + sentiment

**Input Format:**
- Concatenated string of all packet data (space-separated)
- Example: `"audio_chunk_1 audio_chunk_2 audio_chunk_3 ..."`

**Response (Success):**
```json
{
  "transcription": "Customer called about billing issue...",
  "sentiment": "negative",
  "confidence": 0.87
}
```

**Implementation:**
```python
class MockAIService:
    async def transcribe(self, audio_data):
        # 25% failure
        if random.random() < 0.25:
            raise HTTPException(status_code=503)
        
        # 1-3s latency
        await asyncio.sleep(random.uniform(1, 3))
        
        return {
            "transcription": "...",
            "sentiment": "positive",
            "confidence": 0.9
        }
```

---

## 4. Data Flow

### 4.1 Packet Ingestion Flow

```
1. PBX sends POST /v1/call/stream/{call_id}
   ↓
2. FastAPI validates input (Pydantic)
   ↓
3. Return 202 Accepted immediately (<50ms)
   ↓
4. Background task starts:
   a. Acquire row lock on call
   b. Validate sequence (check for gaps, duplicates)
   c. Store packet in database
   d. Update call metadata
   e. Release lock
   f. Broadcast to WebSocket clients
```

### 4.2 Call Completion Flow

```
1. PBX sends POST /v1/call/complete/{call_id} with total_packets
   ↓
2. Transition state: IN_PROGRESS → COMPLETED
   ↓
3. Store expected_total_packets in database
   ↓
4. Grace period: Wait 3 seconds for late packets
   ↓
5. Check completeness:
   - Compare total_packets_received vs expected_total_packets
   - Log warning if missing_sequences is not empty
   - Log missing packet numbers for debugging
   ↓
6. Transition state: COMPLETED → PROCESSING_AI
   ↓
7. Background task:
   a. Fetch all packets (ORDER BY sequence)
   b. Concatenate packet data (space-separated string)
   c. Call AI service with exponential backoff retry
   d. Store results (transcription, sentiment)
   e. Transition to ARCHIVED (success) or FAILED (retry exhausted)
   f. Broadcast completion/failure event to WebSocket
```

### 4.3 Race Condition Handling

```
Thread 1 & Thread 2 receive packets for same call_id
   ↓
Thread 1: SELECT ... FOR UPDATE (acquires lock)
Thread 2: SELECT ... FOR UPDATE (waits)
   ↓
Thread 1: Updates call, commits (releases lock)
   ↓
Thread 2: Acquires lock, sees updated values
Thread 2: Updates call, commits
   ↓
Result: No lost updates ✓
```

---

## 5. Key Design Decisions

### 5.1 Non-Blocking Architecture
- **Decision**: Separate acknowledgment from processing
- **Implementation**: Return 202 immediately, queue background tasks
- **Benefit**: Meets <50ms requirement

### 5.2 Row-Level Locking
- **Decision**: Use database locking for race conditions
- **Implementation**: `SELECT ... FOR UPDATE`
- **Benefit**: Simple, correct, sufficient for assessment scale

### 5.3 Missing Packet Handling
- **Decision**: Process AI even with missing packets
- **Rationale**: Partial results > no results
- **Implementation**: Log warning, continue processing

### 5.4 Duplicate Prevention
- **Decision**: Database UNIQUE constraint + idempotent response
- **Implementation**: UNIQUE(call_id, sequence), catch IntegrityError
- **Benefit**: Guaranteed prevention, even under race conditions

### 5.5 State Machine
- **Decision**: Enforce valid transitions with atomic updates
- **Implementation**: UPDATE with WHERE condition on current state
- **Benefit**: Only one thread can transition from given state

### 5.6 Failed Call Handling
- **Decision**: Store in database with FAILED state, no automatic retry
- **Implementation**: After max retries exhausted, transition to FAILED, log error, broadcast to supervisors
- **Benefit**: Audit trail preserved, manual intervention possible
- **Production Enhancement**: Dead letter queue, automatic retry after delay, alerting

---

## 6. Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Web Framework** | FastAPI | Async support, automatic validation, WebSocket support |
| **Database** | PostgreSQL | ACID compliance, array support, row-level locking |
| **ORM** | SQLAlchemy (async) | Connection pooling, async support, type safety |
| **Validation** | Pydantic | Automatic input validation, type checking |
| **Testing** | pytest + httpx | Async test support, HTTP client for integration tests |
| **Language** | Python 3.10+ | Async/await, type hints, rich ecosystem |

---

## 7. Scalability Considerations

### Current Design (Assessment)
- Single instance
- Database connection pool (15 connections)
- Row-level locking for concurrency
- Suitable for: 100-1000 concurrent calls

### Production Enhancements
1. **Horizontal Scaling**: Multiple FastAPI instances behind load balancer
2. **Message Queue**: Redis/RabbitMQ for packet processing queue
3. **Caching**: Redis for call state caching
4. **Database**: Read replicas, connection pooler (PgBouncer)
5. **Monitoring**: Prometheus metrics, distributed tracing
6. **Rate Limiting**: Per-client rate limits

---

## 8. Error Handling Strategy

### Global Exception Handlers

**Implementation: Simple Global Exception Handler**

```python
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
import logging

app = FastAPI(title="PBX Microservice")
logger = logging.getLogger(__name__)

# Handler 1: Database Integrity Errors (Duplicates)
@app.exception_handler(IntegrityError)
async def handle_integrity_error(request: Request, exc: IntegrityError):
    """Handle duplicate packets gracefully"""
    if "unique constraint" in str(exc).lower():
        logger.warning(f"Duplicate packet detected: {exc}")
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "duplicate",
                "message": "Packet already received",
                "ignored": True
            }
        )
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "Database constraint violation"}
    )

# Handler 2: Validation Errors (Invalid State Transitions)
@app.exception_handler(ValueError)
async def handle_value_error(request: Request, exc: ValueError):
    """Handle validation errors (e.g., invalid state transitions)"""
    logger.warning(f"Validation error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": str(exc)}
    )

# Handler 3: Catch-All for Unexpected Errors
@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    """Catch-all for unexpected errors"""
    logger.error(f"Unexpected error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error"}
    )
```

**Benefits:**
- ✅ Consistent error response format
- ✅ Centralized error logging
- ✅ Clean endpoint code (no repetitive try-catch)
- ✅ Production-ready error handling

---

### Specific Error Scenarios

### Input Validation
- **Invalid input**: Return 422 Unprocessable Entity (Pydantic automatic)
- **Missing fields**: Pydantic validation error
- **Type mismatch**: Automatic coercion or error

### Database Errors
- **Duplicate packet**: Return 202 Accepted with duplicate flag (IntegrityError handler)
- **Connection loss**: Retry with exponential backoff
- **Deadlock**: Database auto-detects, retry transaction

### AI Service Errors
- **503 Service Unavailable**: Retry with exponential backoff
- **Timeout**: Retry (counted toward max attempts)
- **Max retries exceeded**: Mark call as FAILED

### WebSocket Errors
- **Send failure**: Remove dead connection
- **Client disconnect**: Clean up resources

---

## 9. Testing Strategy

### Unit Tests
- Sequence validation logic
- State machine transitions
- Duplicate detection
- Retry logic

### Integration Tests
1. **Out-of-order packets**: Send 1,2,3,5,4 → verify gap detection
2. **Missing packets**: Send 1,2,3,5 → verify warning logged
3. **Duplicate packets**: Send sequence=4 twice → verify only one stored
4. **Race condition**: Use `asyncio.gather()` to send two packets simultaneously → verify both stored, no lost updates
5. **AI retry**: Mock 503 errors → verify exponential backoff (1s, 2s, 4s, 8s delays)
6. **State transitions**: Test all valid and invalid transitions
7. **WebSocket**: Connect, receive events, disconnect
8. **Grace period**: Send completion signal, send late packet within 3s → verify packet accepted and processed

**Race Condition Test Approach:**
```python
import asyncio
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_race_condition():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Send two packets concurrently using asyncio.gather
        responses = await asyncio.gather(
            client.post(f"/v1/call/stream/{call_id}", json=packet_1),
            client.post(f"/v1/call/stream/{call_id}", json=packet_2)
        )
        # Verify both succeeded and both packets stored
        assert all(r.status_code == 202 for r in responses)
        assert call.total_packets_received == 2
```

### Performance Tests
- Measure API response time (<50ms)
- Test concurrent packet ingestion (100+ simultaneous)
- Verify database locking doesn't cause deadlocks

---

## 10. Monitoring and Observability

### Logging
- **INFO**: Packet received, state transitions, AI calls
- **WARNING**: Missing packets, gaps detected, duplicates
- **ERROR**: AI failures, database errors, timeouts

### Metrics (Future)
- Packets received per second
- Average response time
- AI success/failure rate
- Active WebSocket connections
- Database connection pool usage

### Structured Logging Format
```json
{
  "timestamp": "2024-01-30T03:00:00Z",
  "level": "INFO",
  "call_id": "123",
  "event": "packet_received",
  "sequence": 42,
  "total_received": 42
}
```

---

## 11. Security Considerations

### Input Validation
- Validate all input fields (Pydantic)
- Sanitize call_id (prevent SQL injection)
- Limit payload size (prevent DoS)

### Database
- Use parameterized queries (SQLAlchemy)
- Principle of least privilege for DB user
- Connection encryption (SSL)

### API
- Rate limiting (future)
- Authentication/authorization (future)
- CORS configuration

---

## 12. Implementation Phases

### Phase 1: Core Infrastructure
- [ ] FastAPI setup
- [ ] Database schema and connection
- [ ] Basic packet ingestion endpoint
- [ ] State machine implementation

### Phase 2: Required Features
- [ ] Sequence validation (out-of-order, missing)
- [ ] Race condition handling (row locking)
- [ ] Mock AI service with 25% failure
- [ ] Exponential backoff retry logic

### Phase 3: Additional Features
- [ ] Duplicate detection
- [ ] WebSocket supervisor dashboard
- [ ] Call completion endpoint
- [ ] Connection manager

### Phase 4: Testing
- [ ] Unit tests for business logic
- [ ] Integration tests for all edge cases
- [ ] Race condition test (concurrent packets)
- [ ] Performance validation (<50ms)

---

## 14. Success Criteria

### Functional Requirements ✓
- [x] Packet ingestion with <50ms response
- [x] Out-of-order packet detection
- [x] Missing packet logging
- [x] Race condition handling
- [x] AI retry with exponential backoff
- [x] State machine with valid transitions
- [x] WebSocket real-time updates

### Non-Functional Requirements ✓
- [x] Clean code architecture
- [x] Comprehensive error handling
- [x] Proper logging
- [x] Testable components
- [x] Documentation

---

**This high-level design provides a complete blueprint for implementing the PBX microservice assessment.**
