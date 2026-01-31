from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from app.models import Call, Packet
from app.services.call_service import get_call, get_call_with_lock, update_call_state
from app.services.ai_service import call_ai_service, retry_with_exponential_backoff
from app.websocket import manager
from typing import Dict, Any
import asyncio
import logging

logger = logging.getLogger(__name__)

# Constants
MAX_MISSING_SEQUENCES = 100
GRACE_PERIOD_SECONDS = 3

async def validate_and_store_packet(
    db: AsyncSession,
    call_id: str,
    sequence: int,
    data: str,
    timestamp: float
) -> Dict[str, Any]:
    """Validate packet sequence and store with duplicate detection"""
    
    # Get call with lock to prevent race conditions
    call = await get_call_with_lock(db, call_id)
    
    # Create call if doesn't exist
    if not call:
        call = Call(call_id=call_id, state="IN_PROGRESS")
        db.add(call)
        await db.flush()
    
    # Check if this is a duplicate
    is_duplicate = False
    if sequence < call.expected_next_sequence:
        # Could be duplicate or late arrival
        if sequence not in call.missing_sequences:
            # Not in missing list = duplicate
            is_duplicate = True
            logger.warning(f"Call {call_id}: Duplicate packet {sequence}")
            return {
                "status": "duplicate",
                "message": "Packet already received",
                "duplicate": True,
                "sequence": sequence
            }
    
    # Try to store packet
    try:
        packet = Packet(
            call_id=call_id,
            sequence=sequence,
            data=data,
            timestamp=timestamp
        )
        db.add(packet)
        await db.flush()
    except IntegrityError:
        # Database caught duplicate via UNIQUE constraint
        await db.rollback()
        logger.warning(f"Call {call_id}: Duplicate packet {sequence} (caught by DB)")
        return {
            "status": "duplicate",
            "message": "Packet already received",
            "duplicate": True,
            "sequence": sequence
        }
    
    # Update call metadata
    call.total_packets_received += 1
    
    # Handle sequence tracking
    if sequence == call.expected_next_sequence:
        # In-order packet
        call.expected_next_sequence = sequence + 1
        logger.info(f"Call {call_id}: In-order packet {sequence}")
    elif sequence > call.expected_next_sequence:
        # Gap detected - add missing sequences
        missing = list(range(call.expected_next_sequence, sequence))
        
        # Cap missing sequences array
        if len(call.missing_sequences) + len(missing) <= MAX_MISSING_SEQUENCES:
            call.missing_sequences = list(set(call.missing_sequences + missing))
        
        call.expected_next_sequence = sequence + 1
        logger.warning(f"Call {call_id}: Gap detected. Missing packets: {missing}")
    else:
        # Late arrival - remove from missing sequences
        if sequence in call.missing_sequences:
            call.missing_sequences = [s for s in call.missing_sequences if s != sequence]
            logger.info(f"Call {call_id}: Late arrival packet {sequence}")
    
    await db.commit()
    
    # Broadcast packet received event
    await manager.broadcast({
        "event": "packet_received",
        "call_id": call_id,
        "sequence": sequence,
        "total_received": call.total_packets_received,
        "missing_sequences": call.missing_sequences
    })
    
    return {
        "status": "accepted",
        "message": "Packet received successfully",
        "duplicate": False,
        "sequence": sequence,
        "total_received": call.total_packets_received,
        "missing_sequences": call.missing_sequences
    }

async def process_call_completion(
    db: AsyncSession,
    call_id: str,
    total_packets: int
):
    """Process call completion with grace period and AI processing"""
    
    # Transition to COMPLETED
    await update_call_state(db, call_id, "COMPLETED")
    
    # Store expected total packets
    call = await get_call(db, call_id)
    call.expected_total_packets = total_packets
    await db.commit()
    
    # Grace period for late packets
    logger.info(f"Call {call_id}: Grace period ({GRACE_PERIOD_SECONDS}s) for late packets")
    await asyncio.sleep(GRACE_PERIOD_SECONDS)
    
    # Check completeness
    call = await get_call(db, call_id)
    if call.missing_sequences:
        logger.warning(
            f"Call {call_id}: Incomplete - Missing packets: {call.missing_sequences}"
        )
    else:
        logger.info(f"Call {call_id}: All packets received")
    
    # Transition to PROCESSING_AI
    await update_call_state(db, call_id, "PROCESSING_AI")
    
    # Fetch all packets and concatenate data
    result = await db.execute(
        select(Packet)
        .where(Packet.call_id == call_id)
        .order_by(Packet.sequence)
    )
    packets = result.scalars().all()
    audio_data = " ".join([p.data for p in packets])
    
    # Call AI service with retry
    ai_result = await retry_with_exponential_backoff(
        lambda: call_ai_service(audio_data)
    )
    
    if ai_result:
        # AI processing succeeded
        call = await get_call(db, call_id)
        call.transcription = ai_result["transcription"]
        call.sentiment = ai_result["sentiment"]
        await db.commit()
        
        await update_call_state(db, call_id, "ARCHIVED")
        
        # Broadcast AI completion
        await manager.broadcast({
            "event": "ai_completed",
            "call_id": call_id,
            "transcription": ai_result["transcription"],
            "sentiment": ai_result["sentiment"]
        })
        
        logger.info(f"Call {call_id}: AI processing completed successfully")
    else:
        # AI processing failed after retries
        await update_call_state(db, call_id, "FAILED")
        
        # Broadcast AI failure
        await manager.broadcast({
            "event": "ai_failed",
            "call_id": call_id,
            "reason": "AI service failed after maximum retries"
        })
        
        logger.error(f"Call {call_id}: AI processing failed after retries")