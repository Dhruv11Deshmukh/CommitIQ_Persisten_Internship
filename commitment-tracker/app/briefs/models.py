from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, UniqueConstraint
from app.database import Base


class Brief(Base):
    __tablename__ = "briefs"
    id = Column(Integer, primary_key=True)
    kind = Column(String(10), nullable=False)          # daily | weekly
    period_key = Column(String(20), nullable=False)    # 2026-11-17 | 2026-W47
    content = Column(Text, nullable=False)             # JSON
    ai_written = Column(Integer, default=0)            # 1 = Gemini wrote headline/summary/actions
    created_at = Column(DateTime, default=datetime.now)
    __table_args__ = (UniqueConstraint("kind", "period_key", name="uq_brief_period"),)