from datetime import datetime, timezone
from .db import get_db

async def audit(tenant_id, actor, action, entity, entity_id=None, details=None, event_key=None):
    """Persist an audit event; event_key makes retry/recovery idempotent."""
    db = get_db()
    doc = {
        "tenant_id": tenant_id,
        "actor_id": str(actor.get("_id", "system")),
        "actor_phone": actor.get("phone"),
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "details": details or {},
        "created_at": datetime.now(timezone.utc),
    }
    if event_key:
        await db.audit_logs.update_one(
            {"tenant_id": tenant_id, "event_key": event_key},
            {"$setOnInsert": {**doc, "event_key": event_key}},
            upsert=True,
        )
    else:
        await db.audit_logs.insert_one(doc)
