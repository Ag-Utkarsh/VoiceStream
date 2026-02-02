from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.database import engine, Base
from app.routes import router
from app.exceptions import register_exception_handlers

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Application Lifespan (Startup/Shutdown)
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: create tables on startup, cleanup on shutdown"""
    # Startup
    logger.info("Starting VoiceStream PBX Microservice...")
    async with engine.begin() as conn:
        # Check if tables exist
        def check_tables_exist(connection):
            from sqlalchemy import inspect
            inspector = inspect(connection)
            existing_tables = inspector.get_table_names()
            expected_tables = {'calls', 'packets'}
            return expected_tables.issubset(set(existing_tables))
        
        tables_existed = await conn.run_sync(check_tables_exist)
        
        # Create tables (only creates if they don't exist)
        await conn.run_sync(Base.metadata.create_all)
        
        if tables_existed:
            logger.info("✓ Using existing database tables (calls, packets)")
        else:
            logger.info("✓ Database tables created successfully (calls, packets)")
    
    
    yield
    
    # Shutdown
    logger.info("Shutting down VoiceStream PBX Microservice...")
    await engine.dispose()
    logger.info("Database connections closed")

# Create FastAPI Application
app = FastAPI(
    title="VoiceStream PBX Microservice",
    description="Real-time audio metadata ingestion and AI processing",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register exception handlers
register_exception_handlers(app)

# Include routes
app.include_router(router)

# Application Entry Point
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )