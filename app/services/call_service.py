from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Call
from app.websocket import manager
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# State Machine Constants
VALID_TRANSITIONS = {
    "IN_PROGRESS": ["COMPLETED"],
    "COMPLETED": ["PROCESSING_AI"],
    "PROCESSING_AI": ["ARCHIVED", "FAILED"],
    "ARCHIVED": [],
    "FAILED": []
}

async def get_call(db: AsyncSession, call_id: str) -> Optional[Call]:
    """Get call by ID"""
    result = await db.execute(select(Call).where(Call.call_id == call_id))
    return result.scalar_one_or_none()

async def get_call_with_lock(db: AsyncSession, call_id: str) -> Optional[Call]:
    """Get call with row-level lock (FOR UPDATE)"""
    result = await db.execute(
        select(Call).where(Call.call_id == call_id).with_for_update()
    )
    return result.scalar_one_or_none()

def validate_state_transition(from_state: str, to_state: str) -> bool:
    """Validate if state transition is allowed"""
    allowed = VALID_TRANSITIONS.get(from_state, [])
    return to_state in allowed

async def update_call_state(db: AsyncSession, call_id: str, new_state: str) -> bool:
    """Update call state with validation"""
    call = await get_call_with_lock(db, call_id)
    if not call:
        logger.error(f"Call {call_id}: Not found for state update")
        return False
    
    if not validate_state_transition(call.state, new_state):
        logger.error(f"Call {call_id}: Invalid transition {call.state} → {new_state}")
        raise ValueError(f"Invalid state transition: {call.state} → {new_state}")
    
    old_state = call.state
    call.state = new_state
    await db.commit()
    
    logger.info(f"Call {call_id}: State transition {old_state} → {new_state}")
    
    # Broadcast state change
    await manager.broadcast({
        "event": "state_changed",
        "call_id": call_id,
        "from_state": old_state,
        "to_state": new_state
    })
    
    return True