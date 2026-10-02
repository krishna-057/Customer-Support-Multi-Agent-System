"""Stable identifiers shared by the synthetic service fixtures."""

from uuid import NAMESPACE_URL, uuid5


def fixture_id(kind: str, number: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"customer-support-fixture/{kind}/{number}"))
