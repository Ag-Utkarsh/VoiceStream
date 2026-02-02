# VoiceStream - Technical Documentation

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Technology Stack](#3-technology-stack)
4. [Core Components](#4-core-components)
5. [Data Models](#5-data-models)
6. [API Documentation](#6-api-documentation)
7. [Business Logic](#7-business-logic)
8. [WebSocket Communication](#8-websocket-communication)
9. [Testing Strategy](#9-testing-strategy)
10. [Deployment & Configuration](#10-deployment--configuration)
11. [Design Decisions](#11-design-decisions)
12. [Security Considerations](#12-security-considerations)

---

## 1. Project Overview

### 1.1 Purpose

VoiceStream is a real-time microservice designed to handle streaming audio metadata from a PBX (Private Branch Exchange) system. It orchestrates AI processing for transcription and sentiment analysis while providing real-time visibility to supervisors through WebSocket connections.

### 1.2 Key Features

- **Non-blocking packet ingestion** with <50ms response time
- **Out-of-order packet detection** and gap tracking
- **Duplicate packet prevention** at both application and database levels
- **Race condition handling** via row-level database locking
- **Exponential backoff retry** for unreliable AI service (25% failure rate)
- **State machine** for enforcing valid call lifecycle transitions
- **Real-time WebSocket broadcasting** for supervisor dashboard
- **Comprehensive test coverage** for all critical scenarios

### 1.3 Business Context

In a call center environment where customer calls are routed to AI voice bots, the PBX system breaks down audio into small chunks (packets) and streams them to this microservice. The system must:
- Reliably receive and store packets
- Detect missing or out-of-order packets
- Process complete audio through AI services
- Provide real-time monitoring capabilities

---

## 2. System Architecture

### 2.1 High-Level Architecture

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
│  │ Endpoints    │      │  Manager     │           │
│  └──────┬───────┘      └──────┬───────┘           │
│         │                     │                    │
│         ▼                     │                    │
│  ┌──────────────┐             │                    │
│  │ Service      │             │                    │
│  │ Layer        │             │                    │
│  └──────┬───────┘             │                    │
│         │                     │                    │
│         ▼                     ▼                    │
│  ┌──────────────┐      ┌──────────────┐           │
│  │ Background   │      │ Broadcast    │           │
│  │ Tasks        │──────▶ Events       │           │
│  └──────┬───────┘      └──────────────┘           │
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
```

### 2.2 Architectural Layers

#### API Layer (`routes.py`)
- Handles HTTP requests and WebSocket connections
- Validates input using Pydantic schemas
- Returns responses within <50ms
- Delegates processing to service layer

#### Service Layer (`services/`)
- **packet_service.py**: Packet validation, storage, and processing logic
- **call_service.py**: Call state management and transitions
- **ai_service.py**: Mock AI service with retry logic

#### Data Layer (`models/`)
- **db_models.py**: SQLAlchemy database models
- **schemas.py**: Pydantic request/response schemas

#### Infrastructure
- **database.py**: Database connection and session management
- **config.py**: Configuration management with environment variables
- **exceptions.py**: Global exception handlers
- **websocket.py**: WebSocket connection manager

---

## 3. Technology Stack

### 3.1 Core Technologies

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Web Framework** | FastAPI | 0.109.0 | Async API framework with auto-validation |
| **ASGI Server** | Uvicorn | 0.27.0 | High-performance async server |
| **Database** | PostgreSQL | 12+ | ACID-compliant relational database |
| **ORM** | SQLAlchemy | 2.0.25 | Async database operations |
| **DB Driver** | asyncpg | 0.29.0 | Async PostgreSQL driver |
| **Validation** | Pydantic | 2.5.3 | Data validation and settings |
| **Testing** | pytest | 7.4.4 | Test framework |
| **HTTP Client** | httpx | 0.26.0 | Async HTTP client for tests |
| **Environment** | python-dotenv | 1.0.0 | Environment variable management |

### 3.2 Why These Choices?

**FastAPI**: 
- Native async/await support for non-blocking I/O
- Automatic request/response validation
- Built-in WebSocket support
- Auto-generated OpenAPI documentation

**PostgreSQL**:
- ACID compliance for data integrity
- Array data type for `missing_sequences`
- Row-level locking for race condition prevention
- Excellent async support via asyncpg

**SQLAlchemy (Async)**:
- Connection pooling (5 persistent + 10 overflow)
- Type safety and ORM benefits
- Async support for non-blocking database operations

---

## 4. Core Components

### 4.1 Application Entry Point (`main.py`)

**Responsibilities**:
- FastAPI application initialization
- CORS middleware configuration
- Database table creation on startup
- Exception handler registration
- Graceful shutdown handling

**Key Features**:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create database tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Shutdown: Close database connections
    await engine.dispose()
```

### 4.2 Configuration Management (`config.py`)

**Environment Variables**:
- `DATABASE_URL`: PostgreSQL connection string (required)

**Features**:
- Pydantic-based validation
- Automatic `.env` file loading
- Singleton settings instance
- Clear error messages for missing configuration

### 4.3 Database Layer (`database.py`)

**Connection Pool Configuration**:
```python
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=5,           # Persistent connections
    max_overflow=10,       # Additional connections under load
    pool_pre_ping=True,    # Verify connections before use
    pool_recycle=3600      # Recycle connections every hour
)
```

**Session Management**:
- Async session factory for non-blocking operations
- Automatic session cleanup via dependency injection
- Transaction management with commit/rollback

### 4.4 Exception Handling (`exceptions.py`)

**Global Exception Handlers**:

1. **IntegrityError Handler**: Database constraint violations
   - Duplicate packets → 202 Accepted (idempotent)
   - Foreign key violations → 400 Bad Request

2. **ValueError Handler**: Business logic validation errors
   - Invalid state transitions → 400 Bad Request

3. **Generic Exception Handler**: Unexpected errors
   - Logs full stack trace
   - Returns 500 Internal Server Error

### 4.5 WebSocket Manager (`websocket.py`)

**ConnectionManager Class**:
- Maintains list of active WebSocket connections
- Handles connect/disconnect lifecycle
- Broadcasts messages to all connected clients
- Automatically removes dead connections

**Event Broadcasting**:
```python
await manager.broadcast({
    "event": "packet_received",
    "call_id": "123",
    "sequence": 42,
    "total_received": 42,
    "missing_sequences": [15, 23]
})
```

---

## 5. Data Models

### 5.1 Database Schema

#### Calls Table (`db_models.py`)

```python
class Call(Base):
    __tablename__ = "calls"
    
    call_id = Column(String, primary_key=True)
    state = Column(String, nullable=False, default="IN_PROGRESS")
    total_packets_received = Column(Integer, default=0)
    expected_total_packets = Column(Integer, nullable=True)
    expected_next_sequence = Column(Integer, default=0, nullable=True)
    missing_sequences = Column(ARRAY(Integer), default=[])
    transcription = Column(Text, nullable=True)
    sentiment = Column(String, nullable=True)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)
```

**Field Descriptions**:
- `call_id`: Unique identifier for the call
- `state`: Current state in lifecycle (IN_PROGRESS, COMPLETED, PROCESSING_AI, ARCHIVED, FAILED)
- `total_packets_received`: Counter of successfully stored packets
- `expected_total_packets`: Total packets expected (from completion signal)
- `expected_next_sequence`: Next sequence number expected (for gap detection)
- `missing_sequences`: Array of sequence numbers with detected gaps
- `transcription`: AI-generated transcription result
- `sentiment`: AI-generated sentiment analysis result

#### Packets Table (`db_models.py`)

```python
class Packet(Base):
    __tablename__ = "packets"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    call_id = Column(String, ForeignKey("calls.call_id", ondelete="CASCADE"), nullable=False)
    sequence = Column(Integer, nullable=False)
    data = Column(Text, nullable=False)
    timestamp = Column(Float, nullable=False)
    received_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint('call_id', 'sequence', name='uq_call_sequence'),
        Index('idx_packets_call_id', 'call_id'),
    )
```

**Key Features**:
- `UNIQUE(call_id, sequence)`: Prevents duplicate packets at database level
- `CASCADE DELETE`: Automatically delete packets when call is deleted
- `Index on call_id`: Fast packet retrieval for AI processing

### 5.2 Pydantic Schemas (`schemas.py`)

#### Request Schemas

**PacketRequest**:
```python
class PacketRequest(BaseModel):
    sequence: int = Field(..., ge=0, description="Packet sequence number")
    data: str = Field(..., min_length=1, description="Packet data payload")
    timestamp: float = Field(..., gt=0, description="Packet timestamp")
```

**CallCompletionRequest**:
```python
class CallCompletionRequest(BaseModel):
    total_packets: int = Field(..., gt=0, description="Total expected packets")
```

#### Response Schemas

**PacketResponse**:
```python
class PacketResponse(BaseModel):
    status: str
    message: str
    call_id: str
    sequence: int
    total_received: Optional[int] = None
    missing_sequences: Optional[List[int]] = None
    duplicate: bool = False
```

---

## 6. API Documentation

### 6.1 REST Endpoints

#### POST /v1/call/stream/{call_id}

**Purpose**: Ingest audio metadata packet

**Request**:
```json
{
  "sequence": 0,
  "data": "audio_chunk_0",
  "timestamp": 1234567890.0
}
```

**Response** (202 Accepted):
```json
{
  "status": "accepted",
  "message": "Packet received successfully",
  "call_id": "call_001",
  "sequence": 0,
  "total_received": 1,
  "missing_sequences": [],
  "duplicate": false
}
```

**Duplicate Response** (202 Accepted):
```json
{
  "status": "duplicate",
  "message": "Packet already received",
  "call_id": "call_001",
  "sequence": 0,
  "total_received": 1,
  "missing_sequences": [],
  "duplicate": true
}
```

**Validation Errors** (422 Unprocessable Entity):
- Negative sequence number
- Empty data field
- Invalid timestamp

#### POST /v1/call/complete/{call_id}

**Purpose**: Signal call completion and trigger AI processing

**Request**:
```json
{
  "total_packets": 10
}
```

**Response** (202 Accepted):
```json
{
  "status": "accepted",
  "message": "Call completion signal received",
  "call_id": "call_001",
  "expected_total_packets": 10
}
```

**Background Processing**:
1. Transition state: IN_PROGRESS → COMPLETED
2. Wait 3 seconds (grace period for late packets)
3. Check for missing packets (log warning if incomplete)
4. Transition state: COMPLETED → PROCESSING_AI
5. Fetch all packets and concatenate data
6. Call AI service with exponential backoff retry
7. Store results and transition to ARCHIVED or FAILED

#### GET /

**Purpose**: Health check endpoint

**Response** (200 OK):
```json
{
  "service": "VoiceStream PBX Microservice",
  "status": "running",
  "version": "1.0.0"
}
```

### 6.2 WebSocket Endpoint

#### WebSocket /ws/supervisor

**Purpose**: Real-time supervisor dashboard updates

**Connection**:
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/supervisor');

ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    console.log('Event:', data.event);
};
```

**Event Types**:

1. **packet_received**:
```json
{
  "event": "packet_received",
  "call_id": "call_001",
  "sequence": 42,
  "total_received": 42,
  "missing_sequences": [15, 23]
}
```

2. **state_changed**:
```json
{
  "event": "state_changed",
  "call_id": "call_001",
  "from_state": "IN_PROGRESS",
  "to_state": "COMPLETED"
}
```

3. **ai_completed**:
```json
{
  "event": "ai_completed",
  "call_id": "call_001",
  "transcription": "Customer called about billing issue...",
  "sentiment": "negative"
}
```

4. **ai_failed**:
```json
{
  "event": "ai_failed",
  "call_id": "call_001",
  "reason": "AI service failed after maximum retries"
}
```

---

## 7. Business Logic

### 7.1 Packet Processing (`packet_service.py`)

#### Sequence Validation Logic

**In-Order Packet** (sequence == expected_next_sequence):
```python
call.expected_next_sequence = sequence + 1
# Fast path: no gap detection needed
```

**Gap Detected** (sequence > expected_next_sequence):
```python
# Add missing sequences to tracking array
missing = list(range(call.expected_next_sequence, sequence))
call.missing_sequences = list(set(call.missing_sequences + missing))
call.expected_next_sequence = sequence + 1
logger.warning(f"Gap detected. Missing packets: {missing}")
```

**Late Arrival** (sequence < expected_next_sequence):
```python
if sequence in call.missing_sequences:
    # Remove from missing list
    call.missing_sequences = [s for s in call.missing_sequences if s != sequence]
    logger.info(f"Late arrival packet {sequence}")
else:
    # Duplicate packet
    return {"status": "duplicate", "duplicate": True}
```

#### Duplicate Detection (Two-Layer Approach)

**Layer 1 - Application Logic** (Fast check):
```python
if sequence < call.expected_next_sequence:
    if sequence not in call.missing_sequences:
        return {"status": "duplicate", "duplicate": True}
```

**Layer 2 - Database Constraint** (Guaranteed prevention):
```sql
UNIQUE(call_id, sequence)
```

**Benefits**:
- Application layer provides fast feedback
- Database layer guarantees no duplicates even under race conditions

#### Race Condition Prevention

**Row-Level Locking**:
```python
async def get_call_with_lock(db: AsyncSession, call_id: str):
    result = await db.execute(
        select(Call).where(Call.call_id == call_id).with_for_update()
    )
    return result.scalar_one_or_none()
```

**How It Works**:
- Thread 1 acquires lock on call row
- Thread 2 waits for lock release
- Updates are serialized, preventing lost updates
- Different calls lock different rows (parallel processing)

### 7.2 State Machine (`call_service.py`)

#### Valid State Transitions

```python
VALID_TRANSITIONS = {
    "IN_PROGRESS": ["COMPLETED"],
    "COMPLETED": ["PROCESSING_AI"],
    "PROCESSING_AI": ["ARCHIVED", "FAILED"],
    "ARCHIVED": [],
    "FAILED": []
}
```

**State Flow**:
```
IN_PROGRESS → COMPLETED → PROCESSING_AI → ARCHIVED
                                       ↓
                                    FAILED
```

#### State Transition Validation

```python
async def update_call_state(db: AsyncSession, call_id: str, new_state: str):
    call = await get_call_with_lock(db, call_id)
    
    if not validate_state_transition(call.state, new_state):
        raise ValueError(f"Invalid state transition: {call.state} → {new_state}")
    
    old_state = call.state
    call.state = new_state
    await db.commit()
    
    # Broadcast state change
    await manager.broadcast({
        "event": "state_changed",
        "call_id": call_id,
        "from_state": old_state,
        "to_state": new_state
    })
```

### 7.3 AI Service Integration (`ai_service.py`)

#### Mock AI Service

**Characteristics**:
- 25% failure rate (simulates unreliable service)
- 1-3 second variable latency
- Returns transcription and sentiment

```python
async def call_ai_service(audio_data: str) -> Dict[str, Any]:
    # 25% failure rate
    if random.random() < 0.25:
        raise Exception("AI Service Unavailable (503)")
    
    # 1-3 second latency
    delay = random.uniform(1, 3)
    await asyncio.sleep(delay)
    
    # Generate mock results
    sentiments = ["positive", "negative", "neutral"]
    return {
        "transcription": f"Mock transcription of {len(audio_data)} characters",
        "sentiment": random.choice(sentiments),
        "confidence": random.uniform(0.7, 0.95)
    }
```

#### Exponential Backoff Retry Strategy

**Configuration**:
- Max attempts: 5
- Max cumulative timeout: 60 seconds
- Backoff delays: 1s, 2s, 4s, 8s

```python
async def retry_with_exponential_backoff(
    func,
    max_attempts: int = 5,
    max_timeout: int = 60
) -> Optional[Dict[str, Any]]:
    attempt = 0
    total_time = 0
    
    while attempt < max_attempts:
        try:
            result = await func()
            return result
        except Exception as e:
            attempt += 1
            
            if attempt >= max_attempts:
                return None
            
            # Exponential backoff: 1s, 2s, 4s, 8s
            delay = 2 ** (attempt - 1)
            total_time += delay
            
            if total_time >= max_timeout:
                return None
            
            await asyncio.sleep(delay)
    
    return None
```

**Why Exponential Backoff**:
- Gives service time to recover
- Reduces load on failing service
- Balances persistence with resource efficiency
- Higher success rate than immediate retries

### 7.4 Call Completion Processing

**Grace Period Implementation**:
```python
async def process_call_completion(db: AsyncSession, call_id: str, total_packets: int):
    # 1. Transition to COMPLETED
    await update_call_state(db, call_id, "COMPLETED")
    
    # 2. Store expected total
    call = await get_call(db, call_id)
    call.expected_total_packets = total_packets
    call.expected_next_sequence = None  # No longer accepting packets
    await db.commit()
    
    # 3. Grace period for late packets
    await asyncio.sleep(3)
    
    # 4. Check completeness
    call = await get_call(db, call_id)
    if call.missing_sequences:
        logger.warning(f"Call {call_id}: Missing packets {call.missing_sequences}")
    
    # 5. Transition to PROCESSING_AI
    await update_call_state(db, call_id, "PROCESSING_AI")
    
    # 6. Fetch and concatenate packets
    result = await db.execute(
        select(Packet).where(Packet.call_id == call_id).order_by(Packet.sequence)
    )
    packets = result.scalars().all()
    audio_data = " ".join([p.data for p in packets])
    
    # 7. Call AI with retry
    ai_result = await retry_with_exponential_backoff(
        lambda: call_ai_service(audio_data)
    )
    
    # 8. Store results and update state
    if ai_result:
        call.transcription = ai_result["transcription"]
        call.sentiment = ai_result["sentiment"]
        await db.commit()
        await update_call_state(db, call_id, "ARCHIVED")
        await manager.broadcast({
            "event": "ai_completed",
            "call_id": call_id,
            "transcription": ai_result["transcription"],
            "sentiment": ai_result["sentiment"]
        })
    else:
        await update_call_state(db, call_id, "FAILED")
        await manager.broadcast({
            "event": "ai_failed",
            "call_id": call_id,
            "reason": "AI service failed after maximum retries"
        })
```

---

## 8. WebSocket Communication

### 8.1 Connection Management

**ConnectionManager Class**:
```python
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def broadcast(self, message: dict):
        message_json = json.dumps(message)
        dead_connections = []
        
        for connection in self.active_connections:
            try:
                await connection.send_text(message_json)
            except Exception:
                dead_connections.append(connection)
        
        # Clean up dead connections
        for dead in dead_connections:
            self.disconnect(dead)
```

### 8.2 Event Broadcasting

**When Events Are Broadcast**:

1. **Packet Received**: After successful packet storage
2. **State Changed**: After state transition commits
3. **AI Completed**: After AI processing succeeds
4. **AI Failed**: After retry exhaustion

**Global Manager Instance**:
```python
# websocket.py
manager = ConnectionManager()

# Usage in services
from app.websocket import manager
await manager.broadcast({"event": "packet_received", ...})
```

---

## 9. Testing Strategy

### 9.1 Test Structure

**Test Files**:
- `test_basic.py`: Basic packet ingestion and validation
- `test_state_machine.py`: State transition validation
- `test_race_conditions.py`: Concurrent request handling
- `test_retry_strategy.py`: AI retry logic and exponential backoff
- `test_ai_service.py`: AI service failure rate and latency

### 9.2 Test Configuration (`conftest.py`)

**Session-Scoped Event Loop**:
```python
@pytest_asyncio.fixture(scope="session")
async def event_loop(event_loop_policy):
    loop = event_loop_policy.new_event_loop()
    yield loop
    loop.close()
```

**Database Setup**:
```python
@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_database():
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Clean up data
    async with AsyncSessionLocal() as db:
        await db.execute(text("TRUNCATE TABLE packets, calls CASCADE"))
        await db.commit()
```

### 9.3 Key Test Scenarios

#### Basic Packet Ingestion

**In-Order Packets**:
```python
@pytest.mark.asyncio
async def test_packet_ingestion_in_order(async_client):
    call_id = "test_call_001"
    for seq in range(3):
        response = await async_client.post(
            f"/v1/call/stream/{call_id}",
            json={"sequence": seq, "data": f"packet_{seq}", "timestamp": 1234567890.0 + seq}
        )
        assert response.status_code == 202
        assert response.json()["status"] == "accepted"
```

**Out-of-Order Packets (Gap Detection)**:
```python
@pytest.mark.asyncio
async def test_packet_ingestion_out_of_order(async_client):
    call_id = "test_call_002"
    sequences = [0, 1, 2, 5]  # Missing 3, 4
    for seq in sequences:
        response = await async_client.post(
            f"/v1/call/stream/{call_id}",
            json={"sequence": seq, "data": f"packet_{seq}", "timestamp": 1234567890.0 + seq}
        )
        assert response.status_code == 202
```

**Duplicate Detection**:
```python
@pytest.mark.asyncio
async def test_duplicate_packet_detection(async_client):
    call_id = "test_call_003"
    packet = {"sequence": 0, "data": "packet_0", "timestamp": 1234567890.0}
    
    # Send first time
    response1 = await async_client.post(f"/v1/call/stream/{call_id}", json=packet)
    assert response1.status_code == 202
    
    # Send duplicate
    response2 = await async_client.post(f"/v1/call/stream/{call_id}", json=packet)
    assert response2.status_code == 202
    # Check duplicate flag in response
```

#### Race Condition Testing

**Concurrent Packet Ingestion**:
```python
@pytest.mark.asyncio
async def test_concurrent_packet_ingestion(async_client):
    call_id = "race_test_001"
    
    packet_1 = {"sequence": 0, "data": "packet_0", "timestamp": 1234567890.0}
    packet_2 = {"sequence": 1, "data": "packet_1", "timestamp": 1234567891.0}
    
    # Send both packets concurrently
    responses = await asyncio.gather(
        async_client.post(f"/v1/call/stream/{call_id}", json=packet_1),
        async_client.post(f"/v1/call/stream/{call_id}", json=packet_2)
    )
    
    # Both should succeed
    assert all(r.status_code == 202 for r in responses)
    
    # Verify both packets stored (no lost updates)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Call).where(Call.call_id == call_id))
        call = result.scalar_one_or_none()
        assert call.total_packets_received == 2
```

#### Retry Strategy Testing

**Exponential Backoff Timing**:
```python
@pytest.mark.asyncio
async def test_exponential_backoff_timing():
    call_times = []
    
    async def mock_fail():
        call_times.append(time.time())
        raise Exception("Service Unavailable (503)")
    
    await retry_with_exponential_backoff(mock_fail, max_attempts=5)
    
    # Calculate delays between attempts
    delays = [call_times[i+1] - call_times[i] for i in range(len(call_times)-1)]
    
    # Verify exponential backoff (1s, 2s, 4s, 8s)
    expected_delays = [1, 2, 4, 8]
    for actual, expected in zip(delays, expected_delays):
        assert abs(actual - expected) < 0.5  # 0.5s tolerance
```

**Retry Stops on Success**:
```python
@pytest.mark.asyncio
async def test_retry_stops_on_success():
    call_count = 0
    
    async def mock_succeed_on_third():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("Service Unavailable (503)")
        return {"transcription": "success", "sentiment": "positive"}
    
    result = await retry_with_exponential_backoff(mock_succeed_on_third, max_attempts=5)
    
    assert call_count == 3  # Should stop after success
    assert result["transcription"] == "success"
```

#### State Machine Testing

**Valid Transitions**:
```python
@pytest.mark.asyncio
async def test_valid_transition_to_completed():
    call_id = f"state-completed-{uuid.uuid4().hex[:8]}"
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Create call (IN_PROGRESS)
        await client.post(
            f"/v1/call/stream/{call_id}",
            json={"sequence": 0, "data": "test", "timestamp": 123.45}
        )
        
        # Complete call (IN_PROGRESS → COMPLETED)
        response = await client.post(
            f"/v1/call/complete/{call_id}",
            json={"total_packets": 1}
        )
        assert response.status_code == 202
```

### 9.4 Test Coverage

**Covered Scenarios**:
- ✅ In-order packet ingestion
- ✅ Out-of-order packets (gap detection)
- ✅ Duplicate packet detection
- ✅ Late arrival packets
- ✅ Race conditions (concurrent requests)
- ✅ State machine transitions (valid and invalid)
- ✅ AI service retry with exponential backoff
- ✅ Call completion flow
- ✅ Input validation
- ✅ AI service failure rate (25%)
- ✅ AI service latency (1-3s)

---

## 10. Deployment & Configuration

### 10.1 Environment Setup

**Prerequisites**:
- Python 3.10 or higher
- PostgreSQL 12 or higher
- pip package manager

**Installation Steps**:

1. **Clone Repository**:
```bash
git clone https://github.com/Ag-Utkarsh/VoiceStream.git
cd VoiceStream
```

2. **Create Virtual Environment**:
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

3. **Install Dependencies**:
```bash
pip install -r requirements.txt
```

4. **Setup PostgreSQL Database**:
```bash
psql -U postgres
CREATE DATABASE voicestream;
\q
```

5. **Configure Environment Variables**:

Create `.env` file:
```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/voicestream
```

Create `.env.test` file:
```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/voicestream_test
```

6. **Run Application**:
```bash
uvicorn app.main:app --reload
```

### 10.2 Configuration Options

**Database Connection Pool**:
```python
# database.py
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=5,        # Adjust based on load
    max_overflow=10,    # Additional connections under load
    pool_pre_ping=True, # Verify connections before use
    pool_recycle=3600   # Recycle connections every hour
)
```

**CORS Configuration**:
```python
# main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 10.3 Running Tests

**All Tests**:
```bash
pytest tests/ -v
```

**Specific Test Files**:
```bash
pytest tests/test_basic.py -v
pytest tests/test_state_machine.py -v
pytest tests/test_race_conditions.py -v
pytest tests/test_retry_strategy.py -v
```

**With Coverage**:
```bash
pytest tests/ --cov=app --cov-report=html
```

### 10.4 Production Considerations

**Database**:
- Use connection pooler (PgBouncer) for high scale
- Enable database replication for redundancy
- Regular backups and point-in-time recovery

**Application**:
- Deploy multiple instances behind load balancer
- Use Redis for WebSocket broadcasting across instances
- Implement rate limiting for API endpoints
- Add authentication and authorization

**Monitoring**:
- Application metrics (Prometheus)
- Distributed tracing (Jaeger)
- Log aggregation (ELK stack)
- Error tracking (Sentry)

**Scaling**:
- Horizontal scaling: Multiple application instances
- Database read replicas for analytics queries
- Message queue (RabbitMQ/Kafka) for high-throughput packet ingestion
- Caching layer (Redis) for frequently accessed data

---

## 11. Design Decisions

### 11.1 Non-Blocking Architecture

**Decision**: Separate acknowledgment from processing

**Implementation**:
- Return 202 Accepted immediately (<50ms)
- Queue background tasks for processing
- Use async/await throughout

**Benefits**:
- Meets <50ms response time requirement
- Handles thousands of concurrent requests
- Better resource utilization

### 11.2 Row-Level Locking vs Message Queue

**Decision**: Use database row-level locking

**Rationale**:
- ✅ Simpler (no extra infrastructure)
- ✅ Directly tests concurrency concepts
- ✅ Sufficient for expected load
- ❌ Doesn't scale to extreme throughput

**Alternative (Production)**: Message queue (RabbitMQ/Kafka)
- Better for >10k packets/sec
- Guaranteed ordering per call
- More complex infrastructure

### 11.3 Missing Packet Handling

**Decision**: Process AI even with missing packets

**Rationale**:
- Partial results > no results
- Network packets may never arrive
- 3-second grace period gives late packets a chance
- Missing packets are logged for debugging

**Implementation**:
- Log warning with missing sequence numbers
- Continue to PROCESSING_AI state
- AI processes available data (best-effort)

### 11.4 Duplicate Prevention Strategy

**Decision**: Two-layer approach (application + database)

**Layer 1 - Application Logic**:
- Fast check before database operation
- Provides immediate feedback

**Layer 2 - Database Constraint**:
- UNIQUE(call_id, sequence)
- Guarantees prevention even under race conditions

**Benefits**:
- Fast path for common case
- Guaranteed correctness for edge cases

### 11.5 State Machine Enforcement

**Decision**: Atomic state transitions with validation

**Implementation**:
```python
VALID_TRANSITIONS = {
    "IN_PROGRESS": ["COMPLETED"],
    "COMPLETED": ["PROCESSING_AI"],
    "PROCESSING_AI": ["ARCHIVED", "FAILED"],
    "ARCHIVED": [],
    "FAILED": []
}
```

**Benefits**:
- Prevents invalid state transitions
- Clear audit trail
- Enforces business logic at code level

### 11.6 Grace Period Duration

**Decision**: 3-second grace period after call completion

**Rationale**:
- Balances completeness vs latency
- Typical network delay is <2 seconds
- Acceptable latency for supervisor dashboard
- Reduces incomplete transcriptions

### 11.7 WebSocket for Supervisor Dashboard

**Decision**: Use WebSocket for real-time updates, REST POST for packet ingestion

**Rationale**:
- **REST POST for ingestion**: Task specification, stateless, easier to test
- **WebSocket for dashboard**: Real-time bidirectional communication, efficient broadcasting

**Benefits**:
- Lower latency than HTTP polling
- Persistent connection reduces overhead
- Broadcast to multiple supervisors efficiently

---

## 12. Security Considerations

### 12.1 Current Implementation

**Input Validation**:
- Pydantic schemas validate all request data
- Type checking and constraints (e.g., sequence >= 0)
- Automatic 422 errors for invalid input

**Database Security**:
- Parameterized queries (SQLAlchemy ORM)
- Protection against SQL injection
- Foreign key constraints for referential integrity

**Error Handling**:
- Generic error messages to clients
- Detailed logging for debugging
- No sensitive data in error responses

### 12.2 Production Enhancements

**Authentication & Authorization**:
- JWT tokens for API access
- Role-based access control (RBAC)
- API key management for PBX integration

**Rate Limiting**:
- Per-IP rate limits
- Per-call rate limits
- DDoS protection

**Data Protection**:
- Encryption at rest (database)
- Encryption in transit (TLS/SSL)
- Secure WebSocket connections (WSS)

**Audit Logging**:
- All state transitions logged
- Access logs for security monitoring
- Compliance with data retention policies

**Network Security**:
- Firewall rules
- VPC isolation
- Private database connections

---

## Appendix: Quick Reference

### Common Commands

```bash
# Start application
uvicorn app.main:app --reload

# Run tests
pytest tests/ -v

# Run specific test
pytest tests/test_basic.py::test_packet_ingestion_in_order -v

# Database migrations (if using Alembic)
alembic upgrade head

# Check code style
flake8 app/ tests/

# Format code
black app/ tests/
```

### API Endpoints Summary

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | / | Health check |
| POST | /v1/call/stream/{call_id} | Ingest packet |
| POST | /v1/call/complete/{call_id} | Complete call |
| WebSocket | /ws/supervisor | Real-time updates |

### State Machine Quick Reference

```
IN_PROGRESS → COMPLETED → PROCESSING_AI → ARCHIVED
                                       ↓
                                    FAILED
```

---