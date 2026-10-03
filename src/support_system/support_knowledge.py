"""Deterministic evidence retrieval for the synthetic support knowledge base.

The hash embedding keeps local development reproducible without a model API.
It is lexical: unfamiliar paraphrases intentionally escalate rather than guess.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from importlib.resources import files

from sqlalchemy import select
from sqlalchemy.orm import Session

from support_system.crm_models import SupportArticle

DIMENSIONS = 256
REVISION = "2026-10-03"
MIN_CONFIDENCE = 0.62
STOPWORDS = frozenset({
    "a", "an", "and", "are", "can", "do", "does", "for", "from", "how", "i", "if",
    "in", "is", "it", "my", "of", "on", "or", "should", "the", "to", "was", "what",
    "when", "where", "which", "why", "with", "you", "your",
})


def terms(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if word not in STOPWORDS}


def embedding(value: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for word in terms(value):
        slot = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % DIMENSIONS
        vector[slot] += 1.0
    magnitude = math.sqrt(sum(component * component for component in vector))
    return [component / magnitude for component in vector] if magnitude else vector


def articles() -> list[dict[str, str]]:
    path = files("support_system").joinpath("support_articles.json")
    items = json.loads(path.read_text(encoding="utf-8"))
    ids = [item["id"] for item in items]
    if len(items) < 15 or len(items) > 30 or len(ids) != len(set(ids)):
        raise ValueError("Support corpus must contain 15-30 uniquely identified articles")
    return items


def article_embedding(item: dict[str, str]) -> list[float]:
    return embedding(f"{item['title']} {item['question']}")


def seed_articles(session: Session) -> tuple[int, int]:
    """Insert or revise bundled articles without deleting other knowledge records."""
    inserted = updated = 0
    for item in articles():
        existing = session.get(SupportArticle, item["id"])
        fields = {**item, "revision": REVISION, "embedding": article_embedding(item)}
        if existing is None:
            session.add(SupportArticle(**fields))
            inserted += 1
        elif any(getattr(existing, key) != value for key, value in fields.items() if key != "embedding"):
            for key, value in fields.items():
                setattr(existing, key, value)
            updated += 1
    session.commit()
    return inserted, updated


@dataclass(frozen=True)
class Evidence:
    article_id: str
    title: str
    section: str
    revision: str
    confidence: float


@dataclass(frozen=True)
class TechnicalResult:
    status: str
    answer: str | None
    evidence: list[Evidence]
    escalation_reason: str | None


def decide_answer(query: str, candidates: list[tuple[SupportArticle, float]]) -> TechnicalResult:
    """Only return bundled article text when both vector and anchor terms agree."""
    query_terms = terms(query)
    if not query_terms or not candidates:
        return TechnicalResult("escalated", None, [], "no_relevant_evidence")
    article, distance = candidates[0]
    score = max(0.0, min(1.0, 1.0 - float(distance)))
    anchors = terms(f"{article.title} {article.question}")
    overlap = len(query_terms & anchors) / len(query_terms)
    if score < MIN_CONFIDENCE or overlap < 0.5:
        return TechnicalResult("escalated", None, [], "low_retrieval_confidence")
    evidence = Evidence(article.id, article.title, article.section, article.revision, round(score, 3))
    return TechnicalResult("answered", f"{article.answer} [{article.id}]", [evidence], None)


def answer_technical(session: Session, query: str) -> TechnicalResult:
    vector = embedding(query)
    if not any(vector):
        return TechnicalResult("escalated", None, [], "no_relevant_evidence")
    distance = SupportArticle.embedding.cosine_distance(vector)
    candidates = session.execute(select(SupportArticle, distance).order_by(distance).limit(3)).all()
    return decide_answer(query, candidates)
