import pytest
import asyncio
import time
from unittest.mock import patch, AsyncMock
from app.services.ai_service import retry_with_exponential_backoff

@pytest.mark.asyncio
async def test_exponential_backoff_timing():
    """Test exponential backoff pattern (1s, 2s, 4s, 8s)"""
    call_times = []
    call_count = 0
    
    async def mock_fail():
        nonlocal call_count
        call_count += 1
        call_times.append(time.time())
        raise Exception("Service Unavailable (503)")
    
    result = await retry_with_exponential_backoff(mock_fail, max_attempts=5)
    
    # Verify 5 attempts
    assert len(call_times) == 5, f"Expected 5 attempts, got {len(call_times)}"
    
    # Calculate delays between attempts
    delays = [call_times[i+1] - call_times[i] for i in range(len(call_times)-1)]
    
    print(f"\n🕐 Retry Timing Analysis:")
    print(f"Total attempts: {len(call_times)}")
    print(f"Delays: {[f'{d:.2f}s' for d in delays]}")
    
    # Verify exponential backoff (1s, 2s, 4s, 8s)
    expected_delays = [1, 2, 4, 8]
    tolerance = 0.5  # Allow 0.5s tolerance
    
    for i, (actual, expected) in enumerate(zip(delays, expected_delays)):
        assert abs(actual - expected) < tolerance, \
            f"Delay {i+1}: expected ~{expected}s, got {actual:.2f}s"
        print(f"  Delay {i+1}: {actual:.2f}s (expected ~{expected}s) ✓")
    
    print("✅ Exponential backoff verified!")

@pytest.mark.asyncio
async def test_retry_stops_on_success():
    """Test that retry stops when function succeeds"""
    call_count = 0
    call_times = []
    
    async def mock_succeed_on_third():
        nonlocal call_count
        call_count += 1
        call_times.append(time.time())
        
        if call_count < 3:
            raise Exception("Service Unavailable (503)")
        return {"transcription": "success", "sentiment": "positive", "confidence": 0.9}
    
    result = await retry_with_exponential_backoff(mock_succeed_on_third, max_attempts=5)
    
    assert call_count == 3, f"Should stop after success, got {call_count} attempts"
    assert result["transcription"] == "success"
    
    # Verify only 2 delays (between 3 attempts)
    delays = [call_times[i+1] - call_times[i] for i in range(len(call_times)-1)]
    assert len(delays) == 2
    
    print(f"✅ Retry stopped after success on attempt {call_count}")
    print(f"   Delays before success: {[f'{d:.2f}s' for d in delays]}")

@pytest.mark.asyncio
async def test_max_retries_respected():
    """Test that max retries limit is respected"""
    call_count = 0
    
    async def mock_always_fail():
        nonlocal call_count
        call_count += 1
        raise Exception("Service Unavailable (503)")
    
    result = await retry_with_exponential_backoff(mock_always_fail, max_attempts=3)
    assert result is None  # Should return None after exhausting retries
    assert call_count == 3, f"Should attempt exactly 3 times, got {call_count}"
    
    print(f"✅ Max retries respected: {call_count} attempts")

@pytest.mark.asyncio
async def test_retry_timing_accuracy():
    """Test that retry delays are accurate"""
    call_times = []
    call_count = 0
    
    async def mock_fail():
        nonlocal call_count
        call_count += 1
        call_times.append(time.time())
        raise Exception("Service Unavailable (503)")
    
    result = await retry_with_exponential_backoff(mock_fail, max_attempts=4)
    delays = [call_times[i+1] - call_times[i] for i in range(len(call_times)-1)]
    expected = [1, 2, 4]
    
    print(f"\n🎯 Timing Accuracy Check:")
    for i, (actual, exp) in enumerate(zip(delays, expected)):
        error = abs(actual - exp)
        error_pct = (error / exp) * 100
        print(f"  Delay {i+1}: {actual:.3f}s (expected {exp}s, error: {error:.3f}s / {error_pct:.1f}%)")
        
        # Allow 10% error margin
        assert error_pct < 10, f"Timing error too high: {error_pct:.1f}%"
    
    print("✅ Retry timing accuracy verified!")

@pytest.mark.asyncio
async def test_timeout_enforcement():
    """Test that max_timeout is enforced"""
    call_count = 0
    start_time = time.time()
    
    async def mock_slow_fail():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.5)  # Each call takes 0.5s
        raise Exception("Service Unavailable (503)")
    
    # With max_timeout of 3s, should stop before completing all 10 attempts
    # Attempt 1: 0.5s, delay 1s, Attempt 2: 0.5s, delay 2s = 4s > 3s timeout
    result = await retry_with_exponential_backoff(mock_slow_fail, max_attempts=10, max_timeout=3)
    
    elapsed = time.time() - start_time
    
    # Should timeout and not complete all 10 retries
    assert result is None
    assert call_count < 10, f"Should not complete all 10 retries, got {call_count}"
    
    print(f"✅ Timeout enforced: {elapsed:.2f}s elapsed, {call_count} attempts (max 10)")

@pytest.mark.asyncio
async def test_concurrent_retries():
    """Test that multiple concurrent retry operations work correctly"""
    
    async def retry_task(task_id):
        call_count = 0
        
        async def mock_fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Service Unavailable (503)")
            return {"task_id": task_id, "success": True}
        
        result = await retry_with_exponential_backoff(mock_fail_twice, max_attempts=5)
        return result
    
    # Run 5 concurrent retry operations
    tasks = [retry_task(i) for i in range(5)]
    results = await asyncio.gather(*tasks)
    
    assert len(results) == 5
    assert all(r and r["success"] for r in results)
    
    print(f"✅ Concurrent retries successful: {len(results)} tasks")

@pytest.mark.asyncio
async def test_immediate_success():
    """Test that function succeeding on first attempt doesn't retry"""
    call_count = 0
    
    async def mock_immediate_success():
        nonlocal call_count
        call_count += 1
        return {"status": "success"}
    
    result = await retry_with_exponential_backoff(mock_immediate_success, max_attempts=5)
    assert call_count == 1, f"Should only call once on immediate success, got {call_count}"
    assert result["status"] == "success"
    
    print(f"✅ Immediate success: {call_count} attempt only")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])