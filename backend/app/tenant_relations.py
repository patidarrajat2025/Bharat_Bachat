"""Small pure helpers for consistently tenant-scoped financial relations."""
from __future__ import annotations


def scoped_entity_query(tenant_id: str, entity_id, *, member_id=None) -> dict:
    """Build a mandatory tenant-scoped Mongo query for a related entity."""
    query = {"_id": entity_id, "tenant_id": str(tenant_id)}
    if member_id is not None:
        query["member_id"] = str(member_id)
    return query


def relation_matches(row: dict | None, tenant_id: str, *, member_id=None) -> bool:
    """Validate returned relation ownership even if a caller/mock bypasses query filters."""
    if not row or str(row.get("tenant_id")) != str(tenant_id):
        return False
    if member_id is not None and str(row.get("member_id")) != str(member_id):
        return False
    return True
