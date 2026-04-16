from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr


class AuthenticatedUser(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: str
    session_expires_at: datetime

