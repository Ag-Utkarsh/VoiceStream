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
    db: AsyncSession = Depends(get_db)
):
    """
    Ingest audio metadata packet
    
    - **Asynchronous**: Non-blocking I/O with concurrent request handling
    - **Validates**: Sequence order, detects gaps and duplicates
    - **Returns**: Gap detection and duplicate information in response
    - **Performance**: <50ms response time
    """
    
    # Process packet with await (async I/O, non-blocking)
    result = await validate_and_store_packet(
        db,
        call_id,
        packet.sequence,
        packet.data,
        packet.timestamp
    )
    
    # Return with full gap/duplicate information
    return PacketResponse(
        status=result["status"],
        message=result["message"],
        call_id=call_id,
        sequence=result["sequence"],
        total_received=result.get("total_received"),
        missing_sequences=result.get("missing_sequences"),
        duplicate=result.get("duplicate", False)
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
    
    # Wrapper function that creates its own DB session
    async def process_completion_task():
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await process_call_completion(
                db,
                call_id,
                completion.total_packets
            )
    
    # Queue background task
    background_tasks.add_task(process_completion_task)
    
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