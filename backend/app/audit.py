from datetime import datetime, timezone
from .db import get_db

async def audit(tenant_id, actor, action, entity, entity_id=None, details=None):
    await get_db().audit_logs.insert_one({
        "tenant_id": tenant_id,
        "actor_id": str(actor["_id"]),
        "actor_phone": actor.get("phone"),
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "details": details or {},
        "created_at": datetime.now(timezone.utc),
    })
