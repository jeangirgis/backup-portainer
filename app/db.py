import os
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Ensure backup directory exists for SQLite
if settings.STORAGE_BACKEND == "local":
    os.makedirs(settings.LOCAL_BACKUP_DIR, exist_ok=True)

DB_PATH = os.path.join(settings.LOCAL_BACKUP_DIR, "companion.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(
    bind=engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

class Base(DeclarativeBase):
    pass

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Run lightweight migrations for new columns on existing tables
    await _run_migrations()

async def _run_migrations():
    """Add missing columns to existing tables (SQLite ALTER TABLE)."""
    migrations = [
        ("backup_jobs", "storage_backend", "VARCHAR"),
    ]
    async with engine.begin() as conn:
        for table, column, col_type in migrations:
            try:
                # Check if column already exists
                result = await conn.execute(text(f"PRAGMA table_info({table})"))
                existing_cols = [row[1] for row in result.fetchall()]
                if column not in existing_cols:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                    logger.info(f"Migration: added column '{column}' to '{table}'")
            except Exception as e:
                logger.warning(f"Migration skipped for {table}.{column}: {e}")

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
