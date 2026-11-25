from app.models.interaction import Interaction
from sqlalchemy.orm import Session


class MetricsRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, interaction: Interaction) -> Interaction:
        self.db.add(interaction)
        self.db.commit()
        self.db.refresh(interaction)
        return interaction
