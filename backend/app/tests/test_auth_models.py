from app.models.auth import AuthUser


def test_auth_user_has_status_column():
    """Signup approval gate relies on the status column; backend reads it."""
    assert "status" in AuthUser.__mapper__.columns
    column = AuthUser.__mapper__.columns["status"]
    assert column.nullable is False
    assert str(column.type).upper().startswith("VARCHAR")
