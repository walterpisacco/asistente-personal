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
