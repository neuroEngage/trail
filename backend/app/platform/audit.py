import json
from datetime import datetime, timezone
from typing import Optional, Any
from sqlalchemy.orm import Session

from backend.app.platform.models import AuditEvent

def record(
    db: Session,
    actor_id: Optional[str],
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    before: Optional[Any] = None,
    after: Optional[Any] = None
) -> AuditEvent:
    """
    Records an immutable audit event for every state change.
    """
    before_json = json.dumps(before, default=str) if before is not None else None
    after_json = json.dumps(after, default=str) if after is not None else None

    event = AuditEvent(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=before_json,
        after_json=after_json,
        created_at=datetime.now(timezone.utc)
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
