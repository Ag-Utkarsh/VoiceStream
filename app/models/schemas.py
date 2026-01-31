from pydantic import BaseModel, Field
from typing import Optional, List

class PacketRequest(BaseModel):
    """Packet ingestion request"""
    sequence: int = Field(..., ge=0, description="Packet sequence number")
    data: str = Field(..., min_length=1, description="Packet data payload")
    timestamp: float = Field(..., gt=0, description="Packet timestamp")

class CallCompletionRequest(BaseModel):
    """Call completion request"""
    total_packets: int = Field(..., gt=0, description="Total expected packets")

class PacketResponse(BaseModel):
    """Packet ingestion response"""
    status: str
    message: str
    call_id: str
    sequence: int
    total_received: Optional[int] = None
    missing_sequences: Optional[List[int]] = None
    duplicate: bool = False

class CallStatusResponse(BaseModel):
    """Call status response"""
    call_id: str
    state: str
    total_packets_received: int
    expected_total_packets: Optional[int]
    missing_sequences: List[int]
    transcription: Optional[str]
    sentiment: Optional[str]

class WebSocketMessage(BaseModel):
    """WebSocket event message"""
    event: str
    call_id: str
    data: dict