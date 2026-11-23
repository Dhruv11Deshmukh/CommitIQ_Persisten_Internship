from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
from app.database import Base


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    commitment_id = Column(Integer, index=True, nullable=True)
    kind = Column(String(20), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False, default="")
    dedupe_key = Column(String(200), unique=True, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    action_taken = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    deliver_after = Column(DateTime, default=datetime.now)
    delivered_at = Column(DateTime, nullable=True)


class Reminder(Base):
    __tablename__ = "reminders"
    id = Column(Integer, primary_key=True)
    commitment_id = Column(Integer, index=True, nullable=False)
    remind_at = Column(DateTime, nullable=False)
    fired = Column(Boolean, default=False)


class NotificationState(Base):
    __tablename__ = "notification_state"
    key = Column(String(50), primary_key=True)
    value = Column(String(200), nullable=False)