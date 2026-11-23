from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float, ForeignKey, Enum, Boolean
)
from sqlalchemy.orm import relationship
import enum

from app.database import Base


class CommitmentStatus(str, enum.Enum):
    open = "open"
    at_risk = "at_risk"
    overdue = "overdue"
    done = "done"
    cancelled = "cancelled"


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    channel = Column(String, nullable=False)        # "gmail" | "teams" | "transcript"
    external_id = Column(String, nullable=True)       # message / chat slice / transcript chunk id
    raw_text = Column(Text, nullable=False)
    sent_at = Column(DateTime, nullable=True)         # when the message was actually sent
    ingested_at = Column(DateTime, default=datetime.utcnow)

    commitments = relationship("Commitment", back_populates="source")


class Commitment(Base):
    __tablename__ = "commitments"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"))

    owner = Column(String, nullable=False)
    owner_email = Column(String, nullable=True)
    beneficiary = Column(String, nullable=True)
    beneficiary_email = Column(String, nullable=True)
    description = Column(Text, nullable=False)
    due_date = Column(DateTime, nullable=True)
    status = Column(Enum(CommitmentStatus), default=CommitmentStatus.open)

    priority_score = Column(Float, default=0.0)
    priority_reason = Column(String, nullable=True)
    clash_group_id = Column(String, nullable=True)

    completed_at = Column(DateTime, nullable=True)
    last_nudged_at = Column(DateTime, nullable=True)
    gmail_draft_id = Column(String, nullable=True)
    is_important = Column(Boolean, default=False)
    trust_outcome = Column(String, nullable=True)     # 'on_time' | 'late' once finished
    is_meeting = Column(Boolean, default=False)
    calendar_status = Column(String, nullable=True)
    calendar_event_id = Column(String, nullable=True)
    calendar_event_url = Column(String, nullable=True)
    meet_url = Column(String, nullable=True)
    meeting_draft_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # lazy="joined" loads the source in the same query (no extra query per row)
    source = relationship("Source", back_populates="commitments", lazy="joined")

    @property
    def source_channel(self):
        """Where this commitment came from - shown as a label in the UI."""
        return self.source.channel if self.source else None


class TrustScore(Base):
    __tablename__ = "trust_scores"

    id = Column(Integer, primary_key=True)
    person = Column(String, unique=True, nullable=False)
    completed_count = Column(Integer, default=0)      # finished on time
    late_count = Column(Integer, default=0)           # finished late
    missed_count = Column(Integer, default=0)         # currently overdue (recounted live)
    score = Column(Float, default=1.0)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    commitment_id = Column(Integer, ForeignKey("commitments.id"))
    event = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)