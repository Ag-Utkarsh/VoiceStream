from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import PacketRequest, CallCompletionRequest, PacketResponse
from app.services import validate_and_store_packet, process_call_completion, get_call
from app.websocket import manager
import logging

logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

@router.get("/")
async def health_check():
    return {
        "service": "VoiceStream PBX Microservice",
        "status": "running",
        "version": "1.0.0"
    }

@router.post("/v1/call/stream/{call_id}", response_model=PacketResponse, status_code=202)
async def ingest_packet(
    call_id: str,
    packet: PacketRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Ingest audio metadata packet
    
    - **Non-blocking**: Returns 202 Accepted within <50ms
    - **Validates**: Sequence order, detects gaps and duplicates
    - **Background**: Stores packet and updates call metadata asynchronously
    """
    
    # Queue background task for packet processing
    background_tasks.add_task(
        validate_and_store_packet,
        db,
        call_id,
        packet.sequence,
        packet.data,
        packet.timestamp
    )
    
    # Return immediately (non-blocking)
    return PacketResponse(
        status="accepted",
        message="Packet queued for processing",
        call_id=call_id,
        sequence=packet.sequence
    )

@router.post("/v1/call/complete/{call_id}", status_code=202)
async def complete_call(
    call_id: str,
    completion: CallCompletionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Signal call completion
    
    - **Transitions**: IN_PROGRESS → COMPLETED → PROCESSING_AI
    - **Grace Period**: Waits 3 seconds for late packets
    - **AI Processing**: Triggers transcription and sentiment analysis
    """
    
    # Verify call exists
    call = await get_call(db, call_id)
    if not call:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")
    
    # Queue background task for completion processing
    background_tasks.add_task(
        process_call_completion,
        db,
        call_id,
        completion.total_packets
    )
    
    return {
        "status": "accepted",
        "message": "Call completion signal received",
        "call_id": call_id,
        "expected_total_packets": completion.total_packets
    }

@router.websocket("/ws/supervisor")
async def websocket_supervisor(websocket: WebSocket):
    """
    WebSocket endpoint for real-time supervisor dashboard
    
    Broadcasts events:
    - packet_received: New packet ingested
    - state_changed: Call state transition
    - ai_completed: AI processing finished
    - ai_failed: AI processing failed
    """
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)