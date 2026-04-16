from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from app.models import audit_log, auth, broker_dealer, financial_metric  # noqa: E402,F401
