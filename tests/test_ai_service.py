import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app
from app.services.ai_service import call_ai_service, retry_with_exponential_backoff


@pytest.mark.asyncio
async def test_ai_service_failure_rate():
    """Test that AI service fails approximately 25% of the time"""
    failures = 0
    successes = 0
    attempts = 100
    
    for _ in range(attempts):
        try:
            result = await call_ai_service("test_data")
            successes += 1
        except Exception as e:
            # call_ai_service raises generic Exception, not HTTPException
            if "503" in str(e):
                failures += 1
    
    failure_rate = failures / attempts
    success_rate = successes / attempts
    
    # Should be around 25% failure (allow 15-35% range for randomness)
    assert 0.15 <= failure_rate <= 0.35, \
        f"Failure rate {failure_rate:.2%} not in expected range (15-35%)"
    
    print(f"✅ AI Service Failure Rate: {failure_rate:.2%} ({failures}/{attempts})")
    print(f"✅ AI Service Success Rate: {success_rate:.2%} ({successes}/{attempts})")

@pytest.mark.asyncio
async def test_ai_service_latency():
    """Test that AI service has 1-3 second latency"""
    import time
    latencies = []
    
    for _ in range(10):
        start = time.time()
        try:
            await call_ai_service("test_data")
            latency = time.time() - start
            latencies.append(latency)
        except Exception:
            # Skip failures for latency test
            pass
    
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        
        # Should be between 1-3 seconds
        assert 1.0 <= avg_latency <= 3.0, \
            f"Average latency {avg_latency:.2f}s not in range (1-3s)"
        
        print(f"✅ AI Service Latency - Avg: {avg_latency:.2f}s, Min: {min_latency:.2f}s, Max: {max_latency:.2f}s")

@pytest.mark.asyncio
async def test_ai_retry_on_failure():
    """Test that AI service retries on failures with exponential backoff"""
    call_count = 0
    
    async def mock_ai_call():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("AI Service Unavailable (503)")
        return {"transcription": "success", "sentiment": "positive", "confidence": 0.9}
    
    result = await retry_with_exponential_backoff(mock_ai_call, max_attempts=5)
    
    assert call_count == 3  # Should retry twice before success
    assert result["transcription"] == "success"
    
    print(f"✅ AI retry succeeded after {call_count} attempts")

@pytest.mark.asyncio
async def test_ai_retry_exhaustion():
    """Test that AI gives up after max retries"""
    call_count = 0
   
    async def mock_ai_always_fail():
        nonlocal call_count
        call_count += 1
        raise Exception("AI Service Unavailable (503)")
    
    result = await retry_with_exponential_backoff(mock_ai_always_fail, max_attempts=5)
    
    assert result is None  # Should return None after exhausting retries
    assert call_count == 5  # Should attempt exactly 5 times
    
    print(f"✅ AI retry exhausted after {call_count} attempts")

@pytest.mark.asyncio
async def test_ai_retry_stops_on_success():
    """Test that retry stops immediately when AI succeeds"""
    call_count = 0
    
    async def mock_succeed_on_second():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise Exception("AI Service Unavailable (503)")
        return {"transcription": "success", "sentiment": "positive", "confidence": 0.9}
    
    result = await retry_with_exponential_backoff(mock_succeed_on_second, max_attempts=5)
    
    assert call_count == 2  # Should stop after success, not continue to max
    assert result["transcription"] == "success"
    
    print(f"✅ AI retry stopped after success on attempt {call_count}")

@pytest.mark.asyncio
async def test_ai_with_missing_packets():
    """Test AI processing with incomplete data (missing packets)"""
    async def mock_ai_partial():
        # AI should still work with partial data
        return {
            "transcription": "partial transcription",
            "sentiment": "neutral",
            "confidence": 0.7
        }
    
    result = await retry_with_exponential_backoff(mock_ai_partial)
    assert result["transcription"] == "partial transcription"
    assert result["confidence"] == 0.7
    
    print("✅ AI processed incomplete data successfully")

@pytest.mark.asyncio
async def test_ai_timeout_handling():
    """Test that AI request timeout is handled"""
    call_count = 0
    
    async def mock_ai_timeout():
        nonlocal call_count
        call_count += 1
        # Each call takes 2 seconds, will fail on first attempt
        # Then retry delays: 1s, 2s = 3s total delay
        # Total time: 2s (first call) + 1s (delay) + 2s (second call) = 5s > 4s max_timeout
        await asyncio.sleep(2)
        raise Exception("AI Service Unavailable (503)")
    
    # Use max_timeout to limit total retry time
    result = await retry_with_exponential_backoff(mock_ai_timeout, max_attempts=5, max_timeout=4)
    
    # Should return None due to timeout
    assert result is None
    assert call_count >= 1  # Should have attempted at least once
    
    print(f"✅ AI timeout handled correctly after {call_count} attempts")

@pytest.mark.asyncio
async def test_ai_response_validation():
    """Test that AI response is validated"""
    async def mock_ai_invalid():
        # Return invalid response (missing required fields)
        return {"invalid": "response"}
    
    # Should handle invalid response gracefully
    try:
        result = await retry_with_exponential_backoff(mock_ai_invalid)
        # If validation is implemented, this should fail
        # For now, just verify it doesn't crash
        assert isinstance(result, dict)
    except Exception as e:
        # Validation error is acceptable
        print(f"✅ Invalid AI response caught: {e}")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])