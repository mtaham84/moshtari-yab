"""
Common Image Validation, Sanitization, and Security Service.

Enforces:
1. Strict byte size limits (default max 5MB).
2. Deep content decoding via Pillow (rejects HTML disguised as images, scripts, corrupt files).
3. Image dimension & pixel boundaries (prevents Decompression Bombs).
4. Re-encoding and complete metadata/EXIF stripping to prevent payload injection or privacy leaks.
5. Server-side randomized UUID filename generation (prevents path traversal and file collision).
"""
import io
import os
import uuid
import logging
from typing import Tuple, Set, Dict, Any
from PIL import Image, ImageOps, UnidentifiedImageError
from django.conf import settings
from django.core.exceptions import ValidationError, SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404
from django.views.static import serve as static_serve

logger = logging.getLogger(__name__)

# Security Policies
MAX_IMAGE_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMAGE_DIMENSION = 4096  # Max 4096px in either dimension
MAX_IMAGE_PIXELS = 16_000_000  # Max 16 Megapixels
MIN_IMAGE_DIMENSION = 10  # Min 10px

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
FORMAT_TO_EXT = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
}

SUSPICIOUS_SIGNATURES = [
    b"<html",
    b"<!doctype",
    b"<script",
    b"<?php",
    b"<% ",
    b"<svg",
]


def validate_and_sanitize_image(
    uploaded_file,
    max_size_bytes: int = MAX_IMAGE_FILE_SIZE_BYTES,
) -> ContentFile:
    """
    Validates the uploaded file against security policies, strips all metadata,
    re-encodes the image into a clean stream, and returns a sanitized ContentFile.

    Raises ValidationError with descriptive Persian text on failure.
    """
    if not uploaded_file:
        raise ValidationError("فایل تصویر ارسال نشده است.")

    # 1. Byte Size Check
    file_size = getattr(uploaded_file, "size", None)
    if file_size is None:
        try:
            uploaded_file.seek(0, os.SEEK_END)
            file_size = uploaded_file.tell()
            uploaded_file.seek(0)
        except Exception:
            file_size = 0

    if file_size <= 0:
        raise ValidationError("فایل ارسالی خالی است.")

    if file_size > max_size_bytes:
        max_mb = max_size_bytes // (1024 * 1024)
        raise ValidationError(f"حجم فایل تصویر بیش از حد مجاز است (حداکثر {max_mb} مگابایت).")

    # 2. Raw Header & Content Signature Check (Defense against HTML/Script disguised as images)
    try:
        uploaded_file.seek(0)
        header_sample = uploaded_file.read(2048).lower()
        uploaded_file.seek(0)
    except Exception as exc:
        raise ValidationError(f"خطا در خواندن فایل تصویر: {exc}")

    for sig in SUSPICIOUS_SIGNATURES:
        if sig in header_sample:
            raise ValidationError("محتوای فایل نامعتبر است؛ فایل‌های حاوی اسکریپت یا HTML مجاز نیستند.")

    if file_size < 10:
        raise ValidationError("فایل ارسالی بسیار کوچک است.")

    # 3. Decode & Verify Image with Pillow
    try:
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)
        # Verify file integrity without loading all pixels
        img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        logger.warning("Image verification failed: %s", exc)
        raise ValidationError("فایل ارسالی یک تصویر معتبر نیست یا آسیب دیده است.")

    # Re-open after verify() (Pillow verify marks stream as closed/consumed)
    try:
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)
    except Exception as exc:
        raise ValidationError(f"خطا در پردازش تصویر: {exc}")

    detected_format = (img.format or "").upper()
    if detected_format not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError(
            f"فرمت تصویر ({detected_format or 'ناشناس'}) مجاز نیست. فقط فرمت‌های JPG، PNG و WEBP پذیرفته می‌شوند."
        )

    # 4. Dimension & Pixel Boundaries
    width, height = img.size
    total_pixels = width * height
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION or total_pixels > MAX_IMAGE_PIXELS:
        raise ValidationError(
            f"ابعاد تصویر ({width}×{height}) بیش از سقف مجاز است (حداکثر {MAX_IMAGE_DIMENSION}×{MAX_IMAGE_DIMENSION} پیکسل)."
        )

    if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
        raise ValidationError(f"ابعاد تصویر بسیار کوچک است (حداقل {MIN_IMAGE_DIMENSION}×{MIN_IMAGE_DIMENSION} پیکسل).")

    # 5. Metadata Stripping & Re-encoding
    output_io = io.BytesIO()
    clean_format = detected_format
    if clean_format == "JPEG":
        # Ensure RGB mode (strip CMYK, palette, or alpha for JPEG)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img = ImageOps.exif_transpose(img)
        img.save(output_io, format="JPEG", quality=85, optimize=True)
    elif clean_format == "PNG":
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        img = ImageOps.exif_transpose(img)
        img.save(output_io, format="PNG", optimize=True)
    elif clean_format == "WEBP":
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        img = ImageOps.exif_transpose(img)
        img.save(output_io, format="WEBP", quality=85)
    elif clean_format == "GIF":
        img.save(output_io, format="GIF")

    clean_bytes = output_io.getvalue()
    ext = FORMAT_TO_EXT.get(clean_format, ".jpg")
    safe_name = f"{uuid.uuid4().hex}{ext}"

    sanitized_file = ContentFile(clean_bytes, name=safe_name)
    sanitized_file.content_type = f"image/{clean_format.lower()}"
    return sanitized_file


def is_valid_image(uploaded_file) -> bool:
    """Returns True if the file passes validation and sanitization, False otherwise."""
    try:
        validate_and_sanitize_image(uploaded_file)
        return True
    except ValidationError:
        return False
    except Exception:
        return False


def schedule_file_cleanup(storage, file_name: str) -> None:
    """
    Schedules a file deletion to happen only after the current database transaction
    commits successfully. If the transaction rolls back, the file is NOT deleted.
    If no active transaction is open, execution happens immediately via Django's on_commit.
    """
    if not storage or not file_name:
        return

    def _delete():
        try:
            if storage.exists(file_name):
                storage.delete(file_name)
                logger.info("Cleaned up media file on commit: %s", file_name)
        except Exception as exc:
            logger.warning("Error during committed file cleanup for %s: %s", file_name, exc)

    transaction.on_commit(_delete)


def secure_serve_media(request, path: str, document_root: str = None, show_indexes: bool = False):
    """
    Secure media serving view that wraps Django's static.serve with:
    1. Strict security headers (nosniff, sandbox CSP, no-referrer, same-site CORP).
    2. Session/auth cookie isolation (strips Set-Cookie and forbids active script execution).
    3. Content-Disposition: attachment for any non-image content or dangerous extensions.
    4. Path traversal defense.
    """
    if document_root is None:
        document_root = str(settings.MEDIA_ROOT)

    try:
        # Defensive path normalization & traversal check
        clean_path = os.path.normpath(path).lstrip("/\\")
        if clean_path.startswith("..") or "/../" in path or "\\..\\" in path:
            raise Http404("Invalid media path traversal detected.")

        response = static_serve(request, clean_path, document_root=document_root, show_indexes=show_indexes)
    except (Http404, SuspiciousFileOperation) as exc:
        logger.warning("secure_serve_media 404 for path='%s', clean_path='%s', document_root='%s': %s", path, clean_path, document_root, exc)
        raise Http404("Media file not found or invalid path.")

    # 1. Enforce strict security headers
    response["X-Content-Type-Options"] = "nosniff"
    # Strict sandbox CSP forbids scripts, frames, forms, popups
    response["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    response["Referrer-Policy"] = "no-referrer"
    response["Cross-Origin-Resource-Policy"] = "same-site"

    # 2. Prevent session/cookie leakage on media responses
    if hasattr(response, "cookies") and response.cookies:
        response.cookies.clear()
    if "Set-Cookie" in response:
        del response["Set-Cookie"]

    # 3. For any dangerous or non-standard image MIME type, enforce attachment download
    raw_ct = response.get("Content-Type", "")
    content_type = raw_ct.split(";")[0].strip().lower()
    safe_image_types = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/x-icon"}

    if content_type not in safe_image_types:
        base_name = os.path.basename(clean_path)
        response["Content-Disposition"] = f'attachment; filename="{base_name}"'
        if content_type in {"text/html", "application/javascript", "text/javascript", "image/svg+xml", "application/xml"}:
            response["Content-Type"] = "application/octet-stream"

    return response


def get_all_referenced_media_paths() -> Set[str]:
    """
    Collects normalized relative storage paths of all active media files referenced across models in DB.
    """
    referenced = set()
    try:
        from apps.products.models import ProductImage
        for p in ProductImage.objects.exclude(image="").values_list("image", flat=True):
            if p:
                referenced.add(os.path.normpath(str(p)))
    except Exception as exc:
        logger.warning("Could not collect ProductImage references: %s", exc)

    try:
        from apps.businesses.models import Business
        for p in Business.objects.exclude(logo="").values_list("logo", flat=True):
            if p:
                referenced.add(os.path.normpath(str(p)))
        for p in Business.objects.exclude(cover="").values_list("cover", flat=True):
            if p:
                referenced.add(os.path.normpath(str(p)))
    except Exception as exc:
        logger.warning("Could not collect Business references: %s", exc)

    try:
        from apps.core.models import SiteAppearance
        for p in SiteAppearance.objects.exclude(background_image="").values_list("background_image", flat=True):
            if p:
                referenced.add(os.path.normpath(str(p)))
    except Exception as exc:
        logger.warning("Could not collect SiteAppearance references: %s", exc)

    return referenced


def sweep_orphan_media(
    grace_hours: int = 24,
    dry_run: bool = True,
    quarantine: bool = True,
    media_root: str = None,
) -> Dict[str, Any]:
    """
    Sweeper for unreferenced (orphan) files in media directory.
    - grace_hours: files modified within this window are skipped to prevent racing with active uploads.
    - dry_run: if True, only identifies candidates without moving or deleting.
    - quarantine: if True (and dry_run is False), moves orphans to 'quarantine/' rather than permanently deleting.
    """
    import time
    import shutil
    from pathlib import Path

    if media_root is None:
        media_root = str(settings.MEDIA_ROOT)

    root_path = Path(media_root).resolve()
    if not root_path.exists():
        return {
            "scanned": 0, "referenced": 0, "in_grace": 0,
            "orphans": 0, "quarantined": 0, "deleted": 0, "dry_run": dry_run,
            "orphan_list": [],
        }

    quarantine_dir = root_path / "quarantine"
    if not dry_run and quarantine:
        quarantine_dir.mkdir(parents=True, exist_ok=True)

    referenced = get_all_referenced_media_paths()
    now = time.time()
    grace_seconds = grace_hours * 3600

    total_scanned = 0
    active_count = 0
    in_grace_count = 0
    orphan_files = []
    quarantined_count = 0
    deleted_count = 0

    for current_dir, dirnames, filenames in os.walk(str(root_path)):
        # Skip quarantine directory and hidden directories
        rel_dir = os.path.relpath(current_dir, str(root_path))
        if rel_dir == "quarantine" or rel_dir.startswith("quarantine/") or rel_dir.startswith("."):
            continue

        for fname in filenames:
            if fname.startswith("."):
                continue

            full_path = Path(current_dir) / fname
            rel_file_path = os.path.normpath(str(full_path.relative_to(root_path)))
            total_scanned += 1

            if rel_file_path in referenced:
                active_count += 1
                continue

            # Check grace period via mtime
            try:
                mtime = os.path.getmtime(str(full_path))
            except OSError:
                mtime = now

            if (now - mtime) < grace_seconds:
                in_grace_count += 1
                continue

            orphan_files.append(rel_file_path)

            if not dry_run:
                if quarantine:
                    dest_name = f"{int(now)}_{rel_file_path.replace(os.sep, '_')}"
                    dest_path = quarantine_dir / dest_name
                    shutil.move(str(full_path), str(dest_path))
                    quarantined_count += 1
                else:
                    os.remove(str(full_path))
                    deleted_count += 1

    return {
        "scanned": total_scanned,
        "referenced": active_count,
        "in_grace": in_grace_count,
        "orphans": len(orphan_files),
        "quarantined": quarantined_count,
        "deleted": deleted_count,
        "dry_run": dry_run,
        "orphan_list": orphan_files,
    }

