"""收集需求关联的图片，转 base64，供需求分析/用例生成做多模态识别。

图片来源：
- 飞书同步下载的内联图片(content 中 /api/requirements/media/{token} → uploads/feishu_media/{token}.*)
- 图片型需求(req.attachment_path → uploads/{stored_name})
"""
from __future__ import annotations
import base64
import mimetypes
import re
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image as _PILImage
except Exception:  # pragma: no cover
    _PILImage = None

# 视觉输入前对图片做缩放压缩：UI 截图全分辨率过大→多图易超时。降到 ≤1024px + JPEG q78
_MAX_DIM = 1024


def _downscale(data: bytes) -> tuple[bytes, str]:
    if _PILImage is None:
        return data, "image/png"
    try:
        im = _PILImage.open(BytesIO(data)).convert("RGB")
        w, h = im.size
        if max(w, h) > _MAX_DIM:
            s = _MAX_DIM / max(w, h)
            im = im.resize((max(1, int(w * s)), max(1, int(h * s))))
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=78)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return data, "image/png"

_BACKEND = Path(__file__).resolve().parents[2]
_UPLOADS = _BACKEND / "uploads"
_MEDIA = _UPLOADS / "feishu_media"

_TOKEN_RE = re.compile(r"/api/requirements/media/([A-Za-z0-9]+)")


def _encode(fp: Path) -> tuple[str, str] | None:
    try:
        data = fp.read_bytes()
        if not data:
            return None
        proc, mt = _downscale(data)
        return base64.b64encode(proc).decode(), mt
    except OSError:
        return None


def collect_images_by_tokens(tokens: list[str] | None, max_images: int = 6) -> list[tuple[str, str]]:
    """按图片 token 列表精确加载图片(用于选中段只发其包含的图)。"""
    images: list[tuple[str, str]] = []
    for token in (tokens or []):
        if len(images) >= max_images:
            break
        if not str(token).isalnum():
            continue
        matches = list(_MEDIA.glob(f"{token}.*")) if _MEDIA.exists() else []
        if matches:
            enc = _encode(matches[0])
            if enc:
                images.append(enc)
    return images


def collect_requirement_images(req, max_images: int = 6) -> list[tuple[str, str]]:
    """返回 [(base64, media_type)]，最多 max_images 张，控制 token 成本。"""
    images: list[tuple[str, str]] = []

    # 1) 图片型需求的主图
    if getattr(req, "attachment_path", None):
        enc = _encode(_UPLOADS / req.attachment_path)
        if enc:
            images.append(enc)

    # 2) 内容中内联的飞书图片
    for token in _TOKEN_RE.findall(req.content or ""):
        if len(images) >= max_images:
            break
        matches = list(_MEDIA.glob(f"{token}.*")) if _MEDIA.exists() else []
        if matches:
            enc = _encode(matches[0])
            if enc:
                images.append(enc)

    return images[:max_images]
