from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class CommitmentOut(BaseModel):
    id: int
    owner: str
    beneficiary: Optional[str]
    description: str
    due_date: Optional[datetime]
    status: str
    priority_score: float
    priority_reason: Optional[str] = None
    clash_group_id: Optional[str]
    created_at: datetime
    last_nudged_at: Optional[datetime] = None
    gmail_draft_id: Optional[str] = None
    beneficiary_email: Optional[str] = None
    is_important: bool = False
    source_channel: Optional[str] = None      # "gmail" | "teams" | "transcript"
    is_meeting: bool = False
    calendar_status: Optional[str] = None
    calendar_event_url: Optional[str] = None
    meet_url: Optional[str] = None
    meeting_draft_id: Optional[str] = None

    class Config:
        from_attributes = True


class CommitmentUpdate(BaseModel):
    """All fields optional - only send what you want to change."""
    owner: Optional[str] = None
    beneficiary: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    status: Optional[str] = None  # "open" | "at_risk" | "overdue" | "done" | "cancelled"
    is_important: Optional[bool] = None