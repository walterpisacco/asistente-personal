from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.interaction import Interaction
from app.repositories.metrics_repository import MetricsRepository
from app.schemas.metrics import MetricEvent, MetricEventResponse


class MetricsService:
    def __init__(self, db: Session) -> None:
        self.repo = MetricsRepository(db)

    def track(self, event: MetricEvent) -> MetricEventResponse:
        interaction = Interaction(
            event=event.event,
            user_id=event.user_id,
            character_id=event.character_id,
            conversation_id=event.conversation_id,
            device_id=event.device_id,
            event_metadata=event.metadata or {},
            created_at=event.timestamp or datetime.now(timezone.utc),
        )
        saved = self.repo.create(interaction)
        return MetricEventResponse(ok=True, id=saved.id)
