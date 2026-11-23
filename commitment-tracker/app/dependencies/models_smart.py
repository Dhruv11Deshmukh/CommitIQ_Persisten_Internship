"""Extra tables for smart dependencies. Additive: the existing commitment_dependencies table is untouched."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Float, DateTime
from app.database import Base


class CommitmentKey(Base):
    """A short, human-readable handle for a commitment, e.g. 'system-architecture'."""
    __tablename__ = "commitment_keys"
    id = Column(Integer, primary_key=True)
    commitment_id = Column(Integer, unique=True, index=True, nullable=False)
    key = Column(String(80), unique=True, index=True, nullable=False)


class DependencyMeta(Base):
    """How a link in commitment_dependencies came to exist (auto-detected or manual) and why."""
    __tablename__ = "dependency_meta"
    id = Column(Integer, primary_key=True)
    dep_id = Column(Integer, unique=True, index=True, nullable=False)
    origin = Column(String(10), default="manual")          # auto | manual
    evidence = Column(Text, nullable=True)                 # the sentence it was detected from
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class DependencySuggestion(Base):
    """A dependency found in a message that could not be linked safely on its own (waits for the user)."""
    __tablename__ = "dependency_suggestions"
    id = Column(Integer, primary_key=True)
    commitment_id = Column(Integer, index=True, nullable=False)       # the one that waits
    upstream_commitment_id = Column(Integer, nullable=True)           # best guess, if any
    upstream_owner = Column(String, nullable=True)
    upstream_text = Column(Text, nullable=True)
    evidence = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    source_id = Column(Integer, nullable=True)
    status = Column(String(10), default="pending")                    # pending | linked | tracked | dismissed
    created_at = Column(DateTime, default=datetime.now)


class DependencyScan(Base):
    """Which ingested sources have already been checked for dependencies."""
    __tablename__ = "dependency_scans"
    source_id = Column(Integer, primary_key=True)
    scanned_at = Column(DateTime, default=datetime.now)
    found = Column(Integer, default=0)


class DependencyReminder(Base):
    """A reminder email drafted to someone whose unfinished task other people are waiting on."""
    __tablename__ = "dependency_reminders"
    id = Column(Integer, primary_key=True)
    dedupe_key = Column(String(80), unique=True, index=True, nullable=False)   # "<commitment id>:<due date>"
    commitment_id = Column(Integer, index=True, nullable=False)                # the task that must be completed
    to_email = Column(String, nullable=True)
    draft_id = Column(String, nullable=True)
    waiting_ids = Column(Text, nullable=True)                                  # comma separated downstream ids
    created_at = Column(DateTime, default=datetime.now)
