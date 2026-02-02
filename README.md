# VoiceStream PBX Microservice

A real-time microservice for ingesting streaming audio metadata from a PBX system, orchestrating AI processing for transcription and sentiment analysis, and providing real-time visibility to supervisors via WebSocket.

For more task details, checkout [task.txt](task.txt).

---

## 📋 Table of Contents

- [Methodology](#methodology)
- [Technical Details](#technical-details)
- [Setup Instructions](#setup-instructions)
- [API Usage](#api-usage)
- [WebSocket Connection](#websocket-connection)
- [Testing](#testing)

---

## Methodology

### Approach to the Task

This project implements a **non-blocking, event-driven microservice** designed to handle thousands of concurrent calls with the following approach:

1. **Asynchronous Architecture**: Built with FastAPI and async/await patterns to ensure non-blocking I/O operations, achieving <50ms response times for packet ingestion.

2. **Separation of Concerns**: The application is structured into clear layers:
   - **API Layer** (`routes.py`): Handles HTTP/WebSocket requests
   - **Service Layer** (`services/`): Contains business logic
   - **Data Layer** (`models/`): Database models and schemas
   - **Infrastructure** (`database.py`, `config.py`): Database connections and configuration

3. **Reliability First**: Implements multiple strategies to handle real-world challenges:
   - Out-of-order packet detection and gap tracking
   - Duplicate packet prevention (application + database level)
   - Race condition handling via row-level locking
   - Exponential backoff retry for unreliable AI service

4. **Real-Time Observability**: WebSocket broadcasting provides instant visibility into:
   - Packet ingestion events
   - State transitions
   - AI processing results

5. **State Machine Design**: Enforces valid call lifecycle transitions to prevent invalid states and ensure data consistency.

---

## Technical Details

### Architectural Choices

#### 1. **Non-Blocking Packet Ingestion**

**Challenge**: API must respond within <50ms while processing packets.

**Solution**: 
- Return `202 Accepted` immediately after validation
- Process packet storage and metadata updates asynchronously
- Use database connection pooling (5 persistent + 10 overflow connections)

#### 2. **Race Condition Prevention**

**Challenge**: Multiple packets arriving simultaneously for the same call could cause lost updates.

**Solution**: PostgreSQL row-level locking with `SELECT ... FOR UPDATE`

**How it works**:
- Thread 1 acquires lock on call row
- Thread 2 waits for lock release
- Updates are serialized, preventing lost updates

#### 3. **Duplicate Detection (Two-Layer Approach)**

**Layer 1 - Application Logic** (Fast check):
```python
if sequence < call.expected_next_sequence:
    if sequence not in call.missing_sequences:
        return {"status": "duplicate"}
```

**Layer 2 - Database Constraint** (Guaranteed prevention):
```sql
UNIQUE(call_id, sequence)
```

**Benefits**: Application layer provides fast feedback; database layer guarantees no duplicates even under race conditions.

#### 4. **State Machine with Validation**

**Valid Transitions**:
```
IN_PROGRESS → COMPLETED → PROCESSING_AI → ARCHIVED
                                       ↓
                                    FAILED
```

**Enforcement**:
```python
VALID_TRANSITIONS = {
    "IN_PROGRESS": ["COMPLETED"],
    "COMPLETED": ["PROCESSING_AI"],
    "PROCESSING_AI": ["ARCHIVED", "FAILED"],
    "ARCHIVED": [],
    "FAILED": []
}
```

Prevents invalid transitions (e.g., ARCHIVED → IN_PROGRESS) and ensures data consistency.

#### 5. **Exponential Backoff Retry Strategy**

**Challenge**: AI service has 25% failure rate.

**Solution**: Retry with exponential backoff (1s, 2s, 4s, 8s) up to 5 attempts or 60s timeout.

**Benefits**: Gives service time to recover, reduces load on failing service, higher success rate.

#### 6. **Grace Period for Late Packets**

**Implementation**: 3-second wait after call completion before AI processing.

**Rationale**: Network delays may cause packets to arrive slightly out of order; grace period allows late packets to be included in AI processing.

### Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Web Framework** | FastAPI | Native async support, automatic validation, WebSocket support, auto-generated docs |
| **Database** | PostgreSQL | ACID compliance, array support (missing_sequences), row-level locking |
| **ORM** | SQLAlchemy (async) | Connection pooling, async support, type safety |
| **Validation** | Pydantic | Automatic input validation, type checking, clear error messages |
| **Testing** | pytest + httpx | Async test support, HTTP client for integration tests |
| **Language** | Python 3.10+ | Async/await, type hints, rich ecosystem |

---

## Setup Instructions

### Prerequisites

- Python 3.10 or higher
- PostgreSQL 12 or higher
- pip (Python package manager)

### 1. Clone the Repository

```bash
git clone https://github.com/Ag-Utkarsh/VoiceStream.git
cd VoiceStream
```

### 2. Create Virtual Environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Setup PostgreSQL Database

**Local PostgreSQL**

```bash
# Create database
psql -U postgres
CREATE DATABASE voicestream;
\q
```

### 5. Configure Environment Variables

Create a `.env` and a `.env.test` file in the project root:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/voicestream
```

**Note**: Replace `postgres:postgres` with your actual username:password.

### 6. Run the Application

```bash
uvicorn app.main:app --reload
```

**Expected Output**:
```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Starting VoiceStream PBX Microservice...
INFO:     Database tables created successfully
INFO:     Application startup complete.
```

### 7. Verify Installation

Open your browser and navigate to:
- **API Documentation**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/

---

## API Usage

### 1. Ingest Packet

**Endpoint**: `POST /v1/call/stream/{call_id}`

**Request**:
```bash
curl -X POST "http://localhost:8000/v1/call/stream/call_001" \
  -H "Content-Type: application/json" \
  -d '{
    "sequence": 0,
    "data": "audio_chunk_0",
    "timestamp": 1234567890.0
  }'
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

### 2. Complete Call

**Endpoint**: `POST /v1/call/complete/{call_id}`

**Request**:
```bash
curl -X POST "http://localhost:8000/v1/call/complete/call_001" \
  -H "Content-Type: application/json" \
  -d '{
    "total_packets": 10
  }'
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
1. Waits 3 seconds (grace period for late packets)
2. Checks for missing packets
3. Calls AI service for transcription and sentiment analysis
4. Retries up to 5 times with exponential backoff if AI fails
5. Stores results and transitions to ARCHIVED (or FAILED)

---

## WebSocket Connection

### Connect via Browser Console

#### 1. Open Browser Console

- **Chrome/Edge**: Press `F12` or `Ctrl+Shift+J` (Windows) / `Cmd+Option+J` (Mac)
- **Firefox**: Press `F12` or `Ctrl+Shift+K` (Windows) / `Cmd+Option+K` (Mac)

#### 2. Connect to WebSocket

Paste the following code into the console:

```javascript
// Connect to WebSocket
const ws = new WebSocket('ws://localhost:8000/ws/supervisor');

// Connection opened
ws.onopen = function(event) {
    console.log('✅ Connected to VoiceStream WebSocket');
    console.log('Listening for events...\n');
};

// Listen for messages
ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    console.log('📨 Event Received:', data.event);
    console.log(data);
    console.log('---');
};

// Connection closed
ws.onclose = function(event) {
    console.log('❌ WebSocket connection closed');
};

// Error handler
ws.onerror = function(error) {
    console.error('⚠️ WebSocket error:', error);
};
```

#### 3. Test the Connection

In a new terminal, send some packets:

```bash
# Send packet 0
curl -X POST "http://localhost:8000/v1/call/stream/call_demo" \
  -H "Content-Type: application/json" \
  -d '{"sequence": 0, "data": "chunk_0", "timestamp": 1234567890.0}'
```

#### 4. Expected Console Output

```javascript
✅ Connected to VoiceStream WebSocket
Listening for events...

📨 Event Received: packet_received
{
  event: "packet_received",
  call_id: "call_demo",
  sequence: 0,
  total_received: 1,
  missing_sequences: []
}
---

```

### WebSocket Event Types

| Event | Description | Triggered When |
|-------|-------------|----------------|
| `packet_received` | New packet successfully ingested | Valid packet stored |
| `state_changed` | Call state transition | State machine transition |
| `ai_completed` | AI processing finished | Transcription/sentiment ready |
| `ai_failed` | AI processing failed | Max retries exhausted |

---

## Testing

### Run All Tests

```bash
pytest tests/ -v
```

### Run Specific Test Files

```bash
# Basic packet ingestion tests
pytest tests/test_basic.py -v

# State machine tests
pytest tests/test_state_machine.py -v

# Race condition tests
pytest tests/test_race_conditions.py -v

# AI retry strategy tests
pytest tests/test_retry_strategy.py -v
```

### Test Scenarios Covered

- ✅ In-order packet ingestion
- ✅ Out-of-order packets (gap detection)
- ✅ Duplicate packet detection
- ✅ Late arrival packets
- ✅ Race conditions (concurrent requests)
- ✅ State machine transitions (valid and invalid)
- ✅ AI service retry with exponential backoff
- ✅ Call completion flow
- ✅ Input validation

---

## Project Structure

```
VoiceStream/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI application entry point
│   ├── config.py                # Configuration management
│   ├── database.py              # Database connection & session
│   ├── exceptions.py            # Global exception handlers
│   ├── routes.py                # API endpoints
│   ├── websocket.py             # WebSocket connection manager
│   ├── models/
│   │   ├── db_models.py         # SQLAlchemy database models
│   │   └── schemas.py           # Pydantic request/response schemas
│   └── services/
│       ├── packet_service.py    # Packet validation & processing
│       ├── call_service.py      # Call state management
│       └── ai_service.py        # Mock AI service with retry logic
├── tests/
│   ├── conftest.py              # Pytest configuration
│   ├── test_basic.py            # Basic functionality tests
│   ├── test_state_machine.py   # State transition tests
│   ├── test_retry_strategy.py  # AI retry tests
│   └── test_race_conditions.py # Concurrency tests
├── .env                         # Environment variables (not in git)
├── .env.example                 # Environment template
├── requirements.txt             # Python dependencies
└── README.md                    # This file
```

---

## Key Features

- ✅ **Non-blocking ingestion**: <50ms response time
- ✅ **Out-of-order handling**: Gap detection and late arrival support
- ✅ **Duplicate prevention**: Application + database level
- ✅ **Race condition safe**: Row-level locking
- ✅ **Retry strategy**: Exponential backoff for AI service
- ✅ **State machine**: Enforced call lifecycle
- ✅ **Real-time updates**: WebSocket broadcasting
- ✅ **Comprehensive testing**: Unit + integration tests
- ✅ **Production-ready**: Error handling, logging, validation

---

## Troubleshooting

### Database Connection Error

**Error**: `sqlalchemy.exc.OperationalError: could not connect to server`

**Solution**: 
1. Verify PostgreSQL is running: `psql -U postgres`
2. Check DATABASE_URL in `.env` file
3. Ensure database exists: `CREATE DATABASE voicestream;`

### Port Already in Use

**Error**: `[Errno 10048] error while attempting to bind on address`

**Solution**:
```bash
# Find process using port 8000
netstat -ano | findstr :8000

# Kill the process (Windows)
taskkill /PID <process_id> /F

# Or use a different port
uvicorn app.main:app --port 8001
```

### WebSocket Connection Failed

**Error**: `WebSocket connection to 'ws://localhost:8000/ws/supervisor' failed`

**Solution**:
1. Ensure server is running: `uvicorn app.main:app --reload`
2. Check browser console for CORS errors
3. Verify WebSocket URL is correct