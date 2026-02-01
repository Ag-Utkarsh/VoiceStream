import pytest
import pytest_asyncio
from httpx import AsyncClient
from app.main import app
from app.database import Base, engine, AsyncSessionLocal
from sqlalchemy import text
import asyncio


@pytest_asyncio.fixture(scope="function")
async def async_client():
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_database():
    # Create tables if they don't exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Clean up data after test
    async with AsyncSessionLocal() as db:
        await db.execute(text("TRUNCATE TABLE packets, calls CASCADE"))
        await db.commit()

# Basic Packet Ingestion Tests
@pytest.mark.asyncio
async def test_packet_ingestion_in_order(async_client):
    """Test in-order packet ingestion"""
    call_id = "test_call_001"
    # Send packets in order: 0, 1, 2
    for seq in range(3):
        response = await async_client.post(
            f"/v1/call/stream/{call_id}",
            json={"sequence": seq, "data": f"packet_{seq}", "timestamp": 1234567890.0 + seq}
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["sequence"] == seq

@pytest.mark.asyncio
async def test_packet_ingestion_out_of_order(async_client):
    """Test out-of-order packet ingestion (gap detection)"""
    call_id = "test_call_002"
    sequences = [0, 1, 2, 5]
    for seq in sequences:
        response = await async_client.post(
            f"/v1/call/stream/{call_id}",
            json={"sequence": seq, "data": f"packet_{seq}", "timestamp": 1234567890.0 + seq}
        )
        assert response.status_code == 202

@pytest.mark.asyncio
async def test_duplicate_packet_detection(async_client):
    """Test duplicate packet handling"""
    call_id = "test_call_003"
    # Send packet sequence 0
    response1 = await async_client.post(
        f"/v1/call/stream/{call_id}",
        json={"sequence": 0, "data": "packet_0", "timestamp": 1234567890.0}
    )
    assert response1.status_code == 202
    # Send same packet again (duplicate)
    response2 = await async_client.post(
        f"/v1/call/stream/{call_id}",
        json={"sequence": 0, "data": "packet_0", "timestamp": 1234567890.0}
    )
    assert response2.status_code == 202

@pytest.mark.asyncio
async def test_late_arrival_packet(async_client):
    """Test late arrival packet (arrives after gap detected)"""
    call_id = "test_call_004"
    for seq in [0, 1, 3]:
        await async_client.post(
            f"/v1/call/stream/{call_id}",
            json={"sequence": seq, "data": f"packet_{seq}", "timestamp": 1234567890.0 + seq}
        )
    # Now send packet 2 (late arrival)
    response = await async_client.post(
        f"/v1/call/stream/{call_id}",
        json={"sequence": 2, "data": "packet_2", "timestamp": 1234567892.0}
    )
    assert response.status_code == 202

# Call Completion Tests
@pytest.mark.asyncio
async def test_call_completion(async_client):
    """Test call completion flow"""
    call_id = "test_call_005"
    for seq in range(5):
        await async_client.post(
            f"/v1/call/stream/{call_id}",
            json={"sequence": seq, "data": f"packet_{seq}", "timestamp": 1234567890.0 + seq}
        )
    # Complete the call
    response = await async_client.post(
        f"/v1/call/complete/{call_id}",
        json={"total_packets": 5}
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["call_id"] == call_id

@pytest.mark.asyncio
async def test_call_completion_nonexistent_call(async_client):
    """Test call completion for non-existent call"""
    response = await async_client.post(
        "/v1/call/complete/nonexistent_call",
        json={"total_packets": 10}
    )
    assert response.status_code == 404

# Input Validation Tests
@pytest.mark.asyncio
async def test_invalid_packet_negative_sequence(async_client):
    """Test packet with negative sequence number"""
    response = await async_client.post(
        "/v1/call/stream/test_call",
        json={"sequence": -1, "data": "packet", "timestamp": 1234567890.0}
    )
    assert response.status_code == 422  # Validation error

@pytest.mark.asyncio
async def test_invalid_packet_missing_data(async_client):
    """Test packet with missing data field"""
    response = await async_client.post(
        "/v1/call/stream/test_call",
        json={"sequence": 0, "timestamp": 1234567890.0}
    )
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_invalid_packet_empty_data(async_client):
    """Test packet with empty data"""
    response = await async_client.post(
        "/v1/call/stream/test_call",
        json={"sequence": 0, "data": "", "timestamp": 1234567890.0}
    )
    assert response.status_code == 422