from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from app.models import PacketResponse
import logging

logger = logging.getLogger(__name__)

async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """Handle database integrity errors (duplicates, foreign key violations)"""
    error_msg = str(exc).lower()
    
    if "unique constraint" in error_msg or "duplicate" in error_msg:
        logger.warning(f"Duplicate packet detected: {exc}")
        return JSONResponse(
            status_code=202,  # Accept duplicate as non-error
            content={
                "status": "duplicate",
                "message": "Packet already received",
                "duplicate": True
            }
        )
    
    if "foreign key" in error_msg:
        logger.error(f"Foreign key violation: {exc}")
        return JSONResponse(
            status_code=400,
            content={"detail": "Referenced record does not exist"}
        )
    
    logger.error(f"Database integrity error: {exc}")
    return JSONResponse(
        status_code=400,
        content={"detail": "Database constraint violation"}
    )

async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Handle validation errors (invalid state transitions, bad input)"""
    logger.warning(f"Validation error: {exc}")
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)}
    )

async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unexpected errors"""
    logger.error(f"Unexpected error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )

def register_exception_handlers(app):
    """Register all exception handlers with the FastAPI app"""
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(Exception, generic_exception_handler)