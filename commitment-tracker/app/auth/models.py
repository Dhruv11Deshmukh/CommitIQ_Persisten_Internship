from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class User(Base):
    """A person who can sign in to the dashboard (password and/or Google)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(20), unique=True, nullable=True, index=True)
    display_name = Column(String(80), nullable=False)
    password_hash = Column(String(300), nullable=True)      # empty for Google-only accounts
    google_sub = Column(String(64), unique=True, nullable=True)
    avatar_url = Column(String(500), nullable=True)
    google_token_json = Column(Text, nullable=True)         # OAuth token used for Gmail ingestion
    gmail_connected = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    last_login_at = Column(DateTime, nullable=True)
