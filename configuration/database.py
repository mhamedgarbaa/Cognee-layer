import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from configuration.settings import db_settings

Base = declarative_base()

# -------------------------------
# Async engine
# -------------------------------
engine = create_async_engine(
    db_settings.POSTGRES_URI,
    echo=db_settings.ECHO,
    pool_size=db_settings.POOL_SIZE,
    max_overflow=db_settings.MAX_OVERFLOW,
    pool_timeout=db_settings.POOL_TIMEOUT,
    pool_recycle=db_settings.POOL_RECYCLE,
    future=True,
)

# -------------------------------
# Async session maker
# -------------------------------
async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine, expire_on_commit=False, class_=AsyncSession
)


# -------------------------------
# Dependency for FastAPI
# -------------------------------
async def get_db_session() -> AsyncSession:
    """Async session generator for dependency injection."""
    async with async_session() as session:
        yield session


# -------------------------------
# Optional: startup helper
# -------------------------------
async def init_db():
    """
    Initialize database tables. Call this in FastAPI startup event.
    """
    logging.info("Initializing database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logging.info("Database ready.")


# -------------------------------
# Log database connection (mask password)
# -------------------------------
masked_uri = db_settings.POSTGRES_URI.replace(db_settings.DB_PASSWORD, "***")
logging.info(f"Database engine created: {masked_uri}")
