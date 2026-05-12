"""Endpoints for the Vault file-upload feature.

Five routes mounted under ``/vault/folders/{folder_id}/files``:

    POST /                           upload (multipart)
    GET  /                           list files in this folder
    DELETE /{file_id}                delete file + GCS object + chunks
    GET    /{file_id}/download       302 to a 5-minute signed URL
    POST   /{file_id}/retry          re-run processing on a failed row

Ownership is enforced via the parent ``vault_folder.user_id``; cross-
tenant access surfaces as ``404 vault_folder_not_found`` to mask
existence (same opaque-404 pattern as ``favorite_lists.py`` /
``vault.py``).
"""

from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Path,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.vault_folder import VaultFolder
from app.models.vault_folder_file import VaultFolderFile
from app.schemas.auth import AuthenticatedUser
from app.schemas.vault_files import VaultFolderFileResponse
from app.services.auth import get_current_user
from app.services.vault_processing import process_uploaded_file
from app.services.vault_storage import (
    VaultStorageError,
    delete_object,
    signed_download_url,
    upload_file,
)
from app.services.vault_text_extraction import SUPPORTED_MIME_TYPES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vault/folders/{folder_id}/files")


# Caps mirror plans/vault-rag-2026-05-04.md Section 2.
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_FOLDER = 20
MAX_USER_STORAGE_BYTES = 100 * 1024 * 1024


_FOLDER_NOT_FOUND_DETAIL = "vault_folder_not_found"
_FILE_NOT_FOUND_DETAIL = "vault_folder_file_not_found"


async def _get_owned_folder(
    db: AsyncSession, folder_id: int, user_id: str
) -> VaultFolder:
    """Same opaque-404 pattern as vault.py."""
    result = await db.execute(
        select(VaultFolder).where(
            VaultFolder.id == folder_id,
            VaultFolder.user_id == user_id,
        )
    )
    folder = result.scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail=_FOLDER_NOT_FOUND_DETAIL)
    return folder


async def _get_owned_file(
    db: AsyncSession, folder_id: int, file_id: int, user_id: str
) -> VaultFolderFile:
    """Verify ownership of the file via its folder, return the row."""
    await _get_owned_folder(db, folder_id, user_id)
    result = await db.execute(
        select(VaultFolderFile).where(
            VaultFolderFile.id == file_id,
            VaultFolderFile.folder_id == folder_id,
        )
    )
    file = result.scalar_one_or_none()
    if file is None:
        raise HTTPException(status_code=404, detail=_FILE_NOT_FOUND_DETAIL)
    return file


@router.post("", response_model=VaultFolderFileResponse, status_code=201)
async def upload_vault_file(
    background_tasks: BackgroundTasks,
    folder_id: int = Path(..., ge=1),
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> VaultFolderFile:
    """Receive an upload, validate caps, persist to GCS + DB, queue processing.

    Synchronous part is just receive + validate + store + insert row +
    kick BackgroundTask. Heavy work (extract → chunk → embed) runs
    after the response returns, so an upload of a 10 MB PDF returns in
    well under 5s and the FE polls until the row is ``ready``.
    """
    await _get_owned_folder(db, folder_id, current_user.id)

    mime_type = (file.content_type or "").lower()
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type: {file.content_type or '(none)'}. "
                "Allowed: PDF, DOCX, PPTX, XLSX, TXT, MD, RTF, CSV, HTML, JSON."
            ),
        )

    # Read with a hard byte cap. Reading first then checking len() keeps
    # the code simple; UploadFile.spool_max_size already streams to disk
    # for big bodies, so we're not blowing up memory on huge inputs that
    # we'll reject anyway.
    content = await file.read(MAX_FILE_SIZE_BYTES + 1)
    size_bytes = len(content)
    if size_bytes > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large ({size_bytes} bytes). "
                f"Maximum is {MAX_FILE_SIZE_BYTES} bytes (10 MB)."
            ),
        )
    if size_bytes == 0:
        raise HTTPException(status_code=400, detail="Empty file.")

    # Per-folder file count cap.
    folder_count_stmt = select(func.count(VaultFolderFile.id)).where(
        VaultFolderFile.folder_id == folder_id
    )
    folder_file_count = int((await db.execute(folder_count_stmt)).scalar_one())
    if folder_file_count >= MAX_FILES_PER_FOLDER:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This service already has {MAX_FILES_PER_FOLDER} files. "
                "Delete an existing one to upload another."
            ),
        )

    # Per-user storage cap. Joins back to vault_folder so we only count
    # rows owned by this user across all their folders.
    storage_stmt = (
        select(func.coalesce(func.sum(VaultFolderFile.size_bytes), 0))
        .select_from(VaultFolderFile)
        .join(VaultFolder, VaultFolder.id == VaultFolderFile.folder_id)
        .where(VaultFolder.user_id == current_user.id)
    )
    used = int((await db.execute(storage_stmt)).scalar_one())
    if used + size_bytes > MAX_USER_STORAGE_BYTES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Upload would exceed your {MAX_USER_STORAGE_BYTES // (1024 * 1024)} MB "
                "storage cap. Delete some files and try again."
            ),
        )

    try:
        object_name = await upload_file(
            user_id=current_user.id,
            folder_id=folder_id,
            original_filename=file.filename or "unnamed",
            content=content,
            content_type=mime_type,
        )
    except VaultStorageError as exc:
        logger.error("vault upload: storage error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "File storage is not configured for this environment. "
                "Contact an administrator."
            ),
        ) from exc

    row = VaultFolderFile(
        folder_id=folder_id,
        original_filename=file.filename or "unnamed",
        mime_type=mime_type,
        size_bytes=size_bytes,
        gcs_object_name=object_name,
        processing_status="extracting",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    background_tasks.add_task(process_uploaded_file, row.id)
    return row


@router.get("", response_model=list[VaultFolderFileResponse])
async def list_vault_files(
    folder_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[VaultFolderFile]:
    """Return all files in a folder owned by the caller.

    Newest-first because the typical interaction after upload is "look
    at what I just added" rather than scrolling to the bottom.
    """
    await _get_owned_folder(db, folder_id, current_user.id)
    stmt = (
        select(VaultFolderFile)
        .where(VaultFolderFile.folder_id == folder_id)
        .order_by(VaultFolderFile.created_at.desc(), VaultFolderFile.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.delete("/{file_id}", status_code=204)
async def delete_vault_file(
    folder_id: int = Path(..., ge=1),
    file_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Delete the row, the GCS object, and any chunk rows (cascade)."""
    file_row = await _get_owned_file(db, folder_id, file_id, current_user.id)
    object_name = file_row.gcs_object_name

    await db.execute(
        delete(VaultFolderFile).where(VaultFolderFile.id == file_id)
    )
    await db.commit()

    # Best-effort GCS cleanup. Already-orphaned objects swallow 404
    # inside delete_object; anything else logs but doesn't undo the
    # DB delete (the row was the source of truth and is already gone).
    try:
        await delete_object(object_name)
    except VaultStorageError as exc:
        logger.warning(
            "vault file delete: GCS cleanup failed for %s: %s — DB row dropped",
            object_name,
            exc,
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{file_id}/download")
async def download_vault_file(
    folder_id: int = Path(..., ge=1),
    file_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """302 to a per-request 5-minute V4 signed URL.

    Avoids streaming bytes through the BFF — the browser hits GCS
    directly with the signed URL and we don't pay request-time CPU.
    """
    file_row = await _get_owned_file(db, folder_id, file_id, current_user.id)
    try:
        url = await signed_download_url(file_row.gcs_object_name)
    except VaultStorageError as exc:
        logger.error("vault download: signed URL error: %s", exc)
        raise HTTPException(
            status_code=503, detail="Download is unavailable right now."
        ) from exc
    return RedirectResponse(url, status_code=302)


@router.post("/{file_id}/retry", response_model=VaultFolderFileResponse)
async def retry_vault_file(
    background_tasks: BackgroundTasks,
    folder_id: int = Path(..., ge=1),
    file_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> VaultFolderFile:
    """Re-run processing on a failed row.

    Allowed only when the current status is ``failed`` — retrying a
    ``ready`` or in-flight row is rejected with a 400 to keep the FE
    from racing the orchestrator.
    """
    file_row = await _get_owned_file(db, folder_id, file_id, current_user.id)
    if file_row.processing_status != "failed":
        raise HTTPException(
            status_code=400,
            detail=(
                f"File is in '{file_row.processing_status}' state — "
                "retry only applies to 'failed' files."
            ),
        )
    background_tasks.add_task(process_uploaded_file, file_row.id)
    # Optimistically reflect that processing is in flight again — the
    # orchestrator will overwrite this on first transition.
    file_row.processing_status = "extracting"
    file_row.processing_error = None
    await db.commit()
    await db.refresh(file_row)
    return file_row
