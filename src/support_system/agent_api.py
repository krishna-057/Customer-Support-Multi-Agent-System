"""Agent API shell and guarded technical evidence endpoint."""

import hmac
import os
from collections.abc import Iterator
from dataclasses import asdict
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from support_system.contracts import (
    HealthResponse,
    HealthStatus,
    ServiceName,
    TechnicalAnswerRead,
    TechnicalQuestion,
)
from support_system.crm_models import SupportArticle
from support_system.db import database_url
from support_system.support_knowledge import answer_technical

app = FastAPI(title="Support Agent API", version="0.1.0")


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
def live() -> HealthResponse:
    return HealthResponse(service=ServiceName.AGENT, status=HealthStatus.OK)


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
def ready() -> HealthResponse:
    try:
        engine = create_engine(database_url(), connect_args={"connect_timeout": 2})
        try:
            with Session(engine) as session:
                if session.scalar(select(func.count()).select_from(SupportArticle)):
                    return HealthResponse(service=ServiceName.AGENT, status=HealthStatus.OK)
        finally:
            engine.dispose()
    except (RuntimeError, ValueError, OSError, SQLAlchemyError):
        pass
    raise HTTPException(status_code=503, detail="Knowledge base unavailable")


def knowledge_scope(authorization: Annotated[str | None, Header()] = None) -> None:
    token = os.getenv("TECHNICAL_RETRIEVAL_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="Knowledge base unavailable")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not authorization or not authorization.startswith("Bearer ") or not hmac.compare_digest(
        supplied, token
    ):
        raise HTTPException(status_code=403, detail="Forbidden")


def knowledge_session() -> Iterator[Session]:
    try:
        engine = create_engine(database_url(), connect_args={"connect_timeout": 2})
        with Session(engine) as session:
            yield session
    except (RuntimeError, ValueError, OSError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Knowledge base unavailable") from exc
    finally:
        if "engine" in locals():
            engine.dispose()


@app.post(
    "/internal/technical/answer",
    dependencies=[Depends(knowledge_scope)],
    response_model=TechnicalAnswerRead,
)
def technical_answer(
    question: TechnicalQuestion, session: Annotated[Session, Depends(knowledge_session)]
) -> dict:
    return asdict(answer_technical(session, question.question))
