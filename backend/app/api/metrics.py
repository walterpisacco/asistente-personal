from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.metrics import MetricEvent, MetricEventResponse
from app.services.metrics_service import MetricsService

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.post("", response_model=MetricEventResponse)
def track_metric(
    payload: MetricEvent,
    db: Session = Depends(get_db),
) -> MetricEventResponse:
    return MetricsService(db).track(payload)
