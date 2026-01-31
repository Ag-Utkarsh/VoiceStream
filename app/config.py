from pydantic_settings import BaseSettings
from pydantic import Field
from dotenv import load_dotenv
# Load environment variables from .env file
load_dotenv()


class Settings(BaseSettings):
    """Application configuration with environment variable validation"""
    
    # Database Configuration (Required)
    DATABASE_URL: str = Field(
        ...,
        description="PostgreSQL connection string (postgresql+asyncpg://user:pass@host:port/db)"
    )
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Ignore extra env vars not defined in Settings


# Create singleton settings instance
# This will validate DATABASE_URL is set on import
# and raise a clear ValidationError if missing
settings = Settings()