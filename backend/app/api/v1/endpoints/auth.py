from fastapi import APIRouter, Depends

from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

router = APIRouter()


@router.get("/me", response_model=AuthenticatedUser)
async def read_current_user(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    return current_user

