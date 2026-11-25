from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.actions import Action


class ActionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_clave(self, clave: str) -> Action | None:
        return (
            self.db.query(Action)
            .filter(Action.clave == clave.strip().lower())
            .first()
        )

    def list_all(self) -> list[Action]:
        return self.db.query(Action).order_by(Action.id).all()

    def list_examples(self) -> list[Action]:
        return (
            self.db.query(Action)
            .filter(Action.ejemplos.isnot(None))
            .filter(func.trim(Action.ejemplos) != "")
            .order_by(func.coalesce(Action.orden, 999999).asc(), Action.id.asc())
            .all()
        )
