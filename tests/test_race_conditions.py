"""Race condition tests for concurrent packet ingestion"""

import pytest
from httpx import AsyncClient
from app.main import app
from app.database import Base, engine, AsyncSessionLocal
from app.models import Call
from sqlalchemy import select
import asyncio


@pytest.fixture(scope="function")
async def async_client():
    """Create async test client"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client


@pytest.fixture(scope="function", autouse=True)
async def setup_database():
    """Setup test database before each test"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ============================================================================
# Race Condition Tests (Required by Assessment)
# ============================================================================

@pytest.mark.asyncio
async def test_concurrent_packet_ingestion(async_client):
    """
    Test concurrent packet ingestion for the same call
    
    Simulates race condition: Two packets arriving at the exact same time
    Verifies: No lost updates, correct packet count
    """
    call_id = "race_test_001"
    
    # Create two packets with different sequences
    packet_1 = {"sequence": 0, "data": "packet_0", "timestamp": 1234567890.0}
    packet_2 = {"sequence": 1, "data": "packet_1", "timestamp": 1234567891.0}
    
    # Send both packets concurrently using asyncio.gather
    responses = await asyncio.gather(
        async_client.post(f"/v1/call/stream/{call_id}", json=packet_1),
        async_client.post(f"/v1/call/stream/{call_id}", json=packet_2)
    )
    
    # Both should return 202 Accepted
    assert all(r.status_code == 202 for r in responses)
    
    # Wait for background tasks to complete
    await asyncio.sleep(1)
    
    # Verify both packets were stored (no lost updates)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Call).where(Call.call_id == call_id))
        call = result.scalar_one_or_none()
        
        assert call is not None, "Call should exist"
        assert call.total_packets_received == 2, "Both packets should be counted"
        assert call.expected_next_sequence == 2, "Next sequence should be 2"


@pytest.mark.asyncio
async def test_concurrent_same_sequence_duplicate(async_client):
    """
    Test concurrent submission of the same packet (duplicate)
    
    Verifies: Only one packet is stored, duplicate is detected
    """
    call_id = "race_test_002"
    
    # Same packet sent twice concurrently
    packet = {"sequence": 0, "data": "packet_0", "timestamp": 1234567890.0}
    
    # Send same packet concurrently
    responses = await asyncio.gather(
        async_client.post(f"/v1/call/stream/{call_id}", json=packet),
        async_client.post(f"/v1/call/stream/{call_id}", json=packet)
    )
    
    # Both should return 202 (idempotent)
    assert all(r.status_code == 202 for r in responses)
    
    # Wait for background tasks
    await asyncio.sleep(1)
    
    # Verify only one packet stored
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Call).where(Call.call_id == call_id))
        call = result.scalar_one_or_none()
        
        assert call is not None
        assert call.total_packets_received == 1, "Only one packet should be counted (duplicate rejected)"


@pytest.mark.asyncio
async def test_high_concurrency_multiple_packets(async_client):
    """
    Test high concurrency with multiple packets
    
    Sends 10 packets concurrently for the same call
    Verifies: All packets stored correctly, no lost updates
    """
    call_id = "race_test_003"
    num_packets = 10
    
    # Create 10 different packets
    packets = [
        {"sequence": i, "data": f"packet_{i}", "timestamp": 1234567890.0 + i}
        for i in range(num_packets)
    ]
    
    # Send all packets concurrently
    tasks = [
        async_client.post(f"/v1/call/stream/{call_id}", json=packet)
        for packet in packets
    ]
    responses = await asyncio.gather(*tasks)
    
    # All should succeed
    assert all(r.status_code == 202 for r in responses)
    
    # Wait for all background tasks
    await asyncio.sleep(2)
    
    # Verify all packets stored
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Call).where(Call.call_id == call_id))
        call = result.scalar_one_or_none()
        
        assert call is not None
        assert call.total_packets_received == num_packets, f"All {num_packets} packets should be stored"


@pytest.mark.asyncio
async def test_concurrent_different_calls(async_client):
    """
    Test concurrent packets for different calls
    
    Verifies: Different calls don't interfere with each other
    """
    num_calls = 5
    
    # Create packets for different calls
    tasks = []
    for call_num in range(num_calls):
        call_id = f"race_test_multi_{call_num}"
        packet = {"sequence": 0, "data": f"packet_call_{call_num}", "timestamp": 1234567890.0}
        tasks.append(async_client.post(f"/v1/call/stream/{call_id}", json=packet))
    
    # Send all concurrently
    responses = await asyncio.gather(*tasks)
    
    # All should succeed
    assert all(r.status_code == 202 for r in responses)
    
    # Wait for processing
    await asyncio.sleep(1)
    
    # Verify each call has exactly 1 packet
    async with AsyncSessionLocal() as db:
        for call_num in range(num_calls):
            call_id = f"race_test_multi_{call_num}"
            result = await db.execute(select(Call).where(Call.call_id == call_id))
            call = result.scalar_one_or_none()
            
            assert call is not None, f"Call {call_id} should exist"
            assert call.total_packets_received == 1, f"Call {call_id} should have 1 packet"
