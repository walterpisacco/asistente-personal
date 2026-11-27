from sqlalchemy.orm import Session

from app.models.call_sessions import CallSessions


class CallSessionsRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, row: CallSessions) -> CallSessions:
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row
