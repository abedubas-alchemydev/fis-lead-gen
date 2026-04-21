from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime, timezone
from io import StringIO
import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.executive_contact import ExecutiveContact
from app.schemas.auth import AuthenticatedUser
from app.services.broker_dealers import BrokerDealerRepository

EXPORT_DAILY_LIMIT = 3
EXPORT_ROW_LIMIT = 100


class ExportService:
    def __init__(self) -> None:
        self.repository = BrokerDealerRepository()

    async def get_remaining_exports_today(self, db: AsyncSession, user_id: str) -> int:
        today = datetime.now(timezone.utc).date()
        stmt = select(func.count(AuditLog.id)).where(
            AuditLog.user_id == user_id,
            AuditLog.action == "export_csv",
            func.date(AuditLog.timestamp) == today,
        )
        used = int((await db.execute(stmt)).scalar_one())
        return max(EXPORT_DAILY_LIMIT - used, 0)

    async def build_export(
        self,
        db: AsyncSession,
        *,
        current_user: AuthenticatedUser,
        search: str | None,
        states: list[str],
        statuses: list[str],
        health_statuses: list[str],
        lead_priorities: list[str],
        clearing_partners: list[str],
        clearing_types: list[str],
        list_mode: str,
    ) -> tuple[str, int, int]:
        remaining = await self.get_remaining_exports_today(db, current_user.id)
        if remaining <= 0:
            raise ValueError("Export limit reached (3/day).")

        response = await self.repository.list_broker_dealers(
            db,
            search=search,
            states=states,
            statuses=statuses,
            health_statuses=health_statuses,
            lead_priorities=lead_priorities,
            clearing_partners=clearing_partners,
            clearing_types=clearing_types,
            types_of_business=[],
            list_mode=list_mode,
            sort_by="lead_score",
            sort_dir="desc",
            page=1,
            limit=EXPORT_ROW_LIMIT,
        )

        ids = [item.id for item in response.items]
        contact_names = await self._get_contact_names(db, ids)

        csv_content = self._render_csv(response.items, contact_names)
        await self._log_export(db, current_user, response.meta.total, min(len(response.items), EXPORT_ROW_LIMIT))
        remaining_after = await self.get_remaining_exports_today(db, current_user.id)
        return csv_content, len(response.items), remaining_after

    async def _get_contact_names(self, db: AsyncSession, broker_dealer_ids: list[int]) -> dict[int, str]:
        if not broker_dealer_ids:
            return {}
        stmt = (
            select(ExecutiveContact.bd_id, ExecutiveContact.name)
            .where(ExecutiveContact.bd_id.in_(broker_dealer_ids))
            .order_by(ExecutiveContact.bd_id.asc(), ExecutiveContact.name.asc())
        )
        rows = (await db.execute(stmt)).all()
        grouped: dict[int, list[str]] = defaultdict(list)
        for bd_id, name in rows:
            grouped[int(bd_id)].append(name)
        return {bd_id: ", ".join(names) for bd_id, names in grouped.items()}

    def _render_csv(self, items, contact_names: dict[int, str]) -> str:
        """Render CSV with ONLY the fields permitted by PRD section 7.1.

        Permitted: Broker Name, CIK Identifier, Financial Health Status,
        Net Capital Growth YoY, Current Clearing Arrangements (partner + type),
        Location (City, State), Last Filing Date, FINRA Membership Status,
        Executive Contact Names (names only, NOT email/phone).
        """
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Broker Name",
                "CIK Identifier",
                "Financial Health Status",
                "Net Capital Growth YoY",
                "Current Clearing Arrangements",
                "Location",
                "Last Filing Date",
                "FINRA Membership Status",
                "Executive Contact Names",
            ]
        )

        for item in items:
            # Build clearing arrangements string: "Partner Name (Type)"
            clearing_parts = []
            if item.current_clearing_partner:
                clearing_parts.append(item.current_clearing_partner)
            if item.current_clearing_type:
                type_label = item.current_clearing_type.replace("_", " ").title()
                clearing_parts.append(f"({type_label})")
            clearing_str = " ".join(clearing_parts) if clearing_parts else ""

            # Build location string: "City, State"
            location_parts = [p for p in [item.city, item.state] if p]
            location_str = ", ".join(location_parts)

            writer.writerow(
                [
                    item.name,
                    item.cik or "",
                    item.health_status or "",
                    item.yoy_growth if item.yoy_growth is not None else "",
                    clearing_str,
                    location_str,
                    item.last_filing_date.isoformat() if item.last_filing_date else "",
                    item.status or "",
                    contact_names.get(item.id, ""),
                ]
            )

        writer.writerow([])
        writer.writerow(["Generated by Client Clearing Lead Gen Engine", datetime.now(timezone.utc).isoformat()])
        return output.getvalue()

    async def _log_export(
        self,
        db: AsyncSession,
        current_user: AuthenticatedUser,
        matching_records: int,
        exported_records: int,
    ) -> None:
        db.add(
            AuditLog(
                user_id=current_user.id,
                action="export_csv",
                details=json.dumps(
                    {
                        "matching_records": matching_records,
                        "exported_records": exported_records,
                        "email": current_user.email,
                    }
                ),
            )
        )
        await db.commit()
