"""Brand asset storage on a mounted volume.

Uploaded images are decoded and re-encoded with Pillow, which strips metadata
and neutralises polyglot files. SVG is rejected because it can carry script.
"""

import io
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings
from app.core.errors import Unprocessable

ASSET_KINDS = {
    # kind: (max width/height, output format)
    "logo": (1024, "PNG"),
    "favicon": (256, "PNG"),
    "login_background": (2560, "WEBP"),
}
ACCEPTED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "ICO"}
EXTENSIONS = {"PNG": "png", "WEBP": "webp"}


def _media_root() -> Path:
    root = Path(get_settings().media_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_brand_asset(tenant_id: uuid.UUID, kind: str, data: bytes) -> str:
    s = get_settings()
    if kind not in ASSET_KINDS:
        raise Unprocessable("Unknown asset type", code="invalid_asset")
    if len(data) > s.max_upload_bytes:
        raise Unprocessable(f"File is larger than {s.max_upload_bytes // 1024} KB", code="file_too_large")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()
        if fmt not in ACCEPTED_FORMATS:
            raise Unprocessable("Upload a PNG, JPEG, WEBP, GIF or ICO image", code="invalid_image")
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            if img.width * img.height > 40_000_000:
                raise Unprocessable("Image dimensions are too large", code="invalid_image")
            max_side, out_format = ASSET_KINDS[kind]
            img = img.convert("RGBA") if out_format == "PNG" else img.convert("RGB")
            img.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            img.save(buf, format=out_format, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise Unprocessable("The file is not a valid image", code="invalid_image") from exc

    folder = _media_root() / "tenants" / str(tenant_id)
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{kind}-{uuid.uuid4().hex[:12]}.{EXTENSIONS[out_format]}"
    (folder / filename).write_bytes(buf.getvalue())
    return f"{s.media_url_prefix}/tenants/{tenant_id}/{filename}"


def delete_brand_asset(url: str | None) -> None:
    s = get_settings()
    if not url or not url.startswith(s.media_url_prefix + "/tenants/"):
        return
    relative = url[len(s.media_url_prefix) + 1 :]
    path = (_media_root() / relative).resolve()
    if _media_root().resolve() in path.parents and path.is_file():
        path.unlink(missing_ok=True)
