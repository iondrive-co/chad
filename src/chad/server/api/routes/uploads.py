"""File upload endpoints for screenshots and attachments."""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
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

    # Generate unique filename to avoid collisions
    original_name = file.filename or "screenshot.png"
    ext = Path(original_name).suffix or ".png"
    unique_name = f"{uuid.uuid4().hex}{ext}"

    # Save file
    upload_dir = _get_upload_dir()
    file_path = upload_dir / unique_name

    content = await file.read()
    file_path.write_bytes(content)

    return UploadResponse(path=str(file_path), filename=original_name)
