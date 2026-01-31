from sqlalchemy import Column, String, Integer, Float, ARRAY, Text, TIMESTAMP, Index, UniqueConstraint, ForeignKey
from datetime import datetime
from app.database import Base

class Call(Base):
    """Call metadata and state tracking"""
    __tablename__ = "calls"
    
    call_id = Column(String, primary_key=True)
    state = Column(String, nullable=False, default="IN_PROGRESS")
    total_packets_received = Column(Integer, default=0)
    expected_total_packets = Column(Integer, nullable=True)
    expected_next_sequence = Column(Integer, default=0)
    missing_sequences = Column(ARRAY(Integer), default=[])
    transcription = Column(Text, nullable=True)
    sentiment = Column(String, nullable=True)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

class Packet(Base):
    """Individual packet data"""
    __tablename__ = "packets"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    call_id = Column(String, ForeignKey("calls.call_id", ondelete="CASCADE"), nullable=False)
    sequence = Column(Integer, nullable=False)
    data = Column(Text, nullable=False)
    timestamp = Column(Float, nullable=False)
    received_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint('call_id', 'sequence', name='uq_call_sequence'),
        Index('idx_packets_call_id', 'call_id'),
    )