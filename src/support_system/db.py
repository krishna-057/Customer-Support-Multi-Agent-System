"""CRM database connection and request-scoped sessions."""

import os
from collections.abc import Iterator

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def database_url() -> URL:
    password = os.getenv("DB_PASSWORD")
    if not password:
        raise RuntimeError("DB_PASSWORD is required")
    return URL.create(
        "postgresql+psycopg",
        username=os.getenv("DB_USER", "support"),
        password=password,
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "support"),
    )


def get_session() -> Iterator[Session]:
    try:
        engine = create_engine(
            database_url(), pool_pre_ping=True, connect_args={"connect_timeout": 2}
        )
        with Session(engine) as session:
            yield session
    except (RuntimeError, ValueError, OSError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="CRM unavailable") from exc
    finally:
        if "engine" in locals():
            engine.dispose()
