from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class AuthenticatedUser(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: str
    feature_permissions: list[str] = Field(default_factory=list)
    session_expires_at: datetime

