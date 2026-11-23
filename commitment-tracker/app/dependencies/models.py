from datetime import datetime
from sqlalchemy import Column, Integer, DateTime, UniqueConstraint
from app.database import Base


class CommitmentDependency(Base):
    """commitment_id cannot finish before depends_on_id (upstream) finishes."""
    __tablename__ = "commitment_dependencies"
    id = Column(Integer, primary_key=True)
    commitment_id = Column(Integer, index=True, nullable=False)   # downstream
    depends_on_id = Column(Integer, index=True, nullable=False)   # upstream
    created_at = Column(DateTime, default=datetime.now)
    __table_args__ = (UniqueConstraint("commitment_id", "depends_on_id", name="uq_dep_pair"),)