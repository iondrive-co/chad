"""File upload endpoints for screenshots and attachments."""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel


router = APIRouter()

# Allowed image MIME types
ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
}

# 20MB is far above any real screenshot; caps memory per request
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Magic-byte signatures per format. The client-supplied content_type and
# filename are attacker-controlled, so the bytes decide what we store.
_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
]


def _sniff_image_extension(content: bytes) -> str | None:
    """Return the canonical extension for known image bytes, else None."""
    for signature, ext in _MAGIC_SIGNATURES:
        if content.startswith(signature):
            return ext
    # WEBP: "RIFF" .... "WEBP"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    return None


def _get_upload_dir() -> Path:
    """Get the upload directory, creating it if needed."""
    base_dir = Path(os.environ.get("CHAD_LOG_DIR", Path.home() / ".chad" / "logs"))
    upload_dir = base_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


class UploadResponse(BaseModel):
    """Response model for file upload."""

    path: str
    filename: str
    # URL (relative to the API base) where the uploaded file can be fetched
    url: str


@router.post("", response_model=UploadResponse, status_code=201)
async def upload_file(file: UploadFile = File(...)) -> UploadResponse:
    """Upload a screenshot or image file.

    Returns the absolute path to the uploaded file, which can be passed
    to the task API in the screenshots field.
    """
    # Validate content type
    content_type = file.content_type or ""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Only image files are allowed. Got: {content_type}",
        )

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 20MB)")

    # The extension comes from the actual bytes, never from the client's
    # filename — that also rejects non-images with an image content_type.
    ext = _sniff_image_extension(content)
    if ext is None:
        raise HTTPException(
            status_code=400,
            detail="File content is not a supported image (png, jpeg, gif, webp)",
        )

    # Generate unique filename to avoid collisions
    original_name = file.filename or f"screenshot{ext}"
    unique_name = f"{uuid.uuid4().hex}{ext}"

    # Save file
    upload_dir = _get_upload_dir()
    file_path = upload_dir / unique_name
    file_path.write_bytes(content)

    return UploadResponse(
        path=str(file_path),
        filename=original_name,
        url=f"/api/v1/uploads/{unique_name}",
    )


@router.get("/{filename}")
async def get_upload(filename: str) -> FileResponse:
    """Serve a previously uploaded file by its unique name.

    The React UI uses this to render screenshot thumbnails (it used to point
    at a /api/v1/file endpoint that never existed).
    """
    upload_dir = _get_upload_dir()
    file_path = (upload_dir / filename).resolve()
    # Guard against path traversal — only files directly inside uploads/
    if file_path.parent != upload_dir.resolve() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Upload not found")
    return FileResponse(file_path)
