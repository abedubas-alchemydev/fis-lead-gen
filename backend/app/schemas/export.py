from __future__ import annotations

from pydantic import BaseModel


class ExportPreviewResponse(BaseModel):
    matching_records: int
    export_limit: int
    remaining_exports_today: int
    requested_records: int


class ExportCsvResponse(BaseModel):
    filename: str
    content: str
    exported_records: int
    remaining_exports_today: int
