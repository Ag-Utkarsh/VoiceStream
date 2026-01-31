from typing import Dict, Any, Optional
import asyncio
import random
import logging

logger = logging.getLogger(__name__)

async def call_ai_service(audio_data: str) -> Dict[str, Any]:
    """Mock AI service with 25% failure rate and 1-3s latency"""
    
    # 25% failure rate
    if random.random() < 0.25:
        logger.warning("AI Service: Simulated 503 failure")
        raise Exception("AI Service Unavailable (503)")
    
    # 1-3 second latency
    delay = random.uniform(1, 3)
    await asyncio.sleep(delay)
    
    # Generate mock transcription and sentiment
    sentiments = ["positive", "negative", "neutral"]
    result = {
        "transcription": f"Mock transcription of {len(audio_data)} characters of audio data",
        "sentiment": random.choice(sentiments),
        "confidence": random.uniform(0.7, 0.95)
    }
    
    logger.info(f"AI Service: Success after {delay:.2f}s - Sentiment: {result['sentiment']}")
    return result

async def retry_with_exponential_backoff(
    func,
    max_attempts: int = 5,
    max_timeout: int = 60
) -> Optional[Dict[str, Any]]:
    """Retry function with exponential backoff"""
    
    attempt = 0
    total_time = 0
    
    while attempt < max_attempts:
        try:
            result = await func()
            logger.info(f"Retry: Success on attempt {attempt + 1}")
            return result
        except Exception as e:
            attempt += 1
            
            if attempt >= max_attempts:
                logger.error(f"Retry: Failed after {max_attempts} attempts")
                return None
            
            # Exponential backoff: 1s, 2s, 4s, 8s
            delay = 2 ** (attempt - 1)
            total_time += delay
            
            if total_time >= max_timeout:
                logger.error(f"Retry: Timeout after {total_time}s")
                return None
            
            logger.warning(f"Retry: Attempt {attempt} failed. Retrying in {delay}s...")
            await asyncio.sleep(delay)
    
    return None