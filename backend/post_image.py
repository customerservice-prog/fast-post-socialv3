"""
Generate 1200x630 Facebook share images with Pillow (in-memory JPEG).
"""

from __future__ import annotations

import io
import logging
import textwrap
from pathlib import Path
from typing import Dict, List, Tuple, Union

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 1200, 630

_FONT_DIR = Path(__file__).resolve().parent / "fonts"

# Background RGB by internal post_type
_POST_TYPE_COLORS: Dict[str, Tuple[int, int, int]] = {
    "morning_promo": (28, 45, 92),
    "afternoon_tip": (18, 92, 65),
    "evening_proof": (88, 42, 98),
    "default": (35, 40, 55),
}

_TEXT_LIGHT = (248, 250, 252)
_TEXT_MUTED = (203, 213, 225)


def _font_candidates(bold: bool) -> List[Path]:
    """Prefer bundled font (add DejaVuSans.ttf under backend/fonts/), then OS paths."""
    out: List[Path] = []
    if bold:
        out.append(_FONT_DIR / "DejaVuSans-Bold.ttf")
    out.append(_FONT_DIR / "DejaVuSans.ttf")
    if bold:
        out.extend(
            [
                Path(r"C:\Windows\Fonts\arialbd.ttf"),
                Path(r"C:\Windows\Fonts\calibrib.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
            ]
        )
    out.extend(
        [
            Path(r"C:\Windows\Fonts\arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        ]
    )
    return out


def _try_load_font(size: int, bold: bool) -> Union[ImageFont.FreeTypeFont, ImageFont.ImageFont]:
    for path in _font_candidates(bold):
        try:
            if path.is_file():
                return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _minimal_fallback_jpeg(business_name: str) -> bytes:
    """Last resort if layout fails (still a valid image for Graph API)."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (40, 40, 55))
    d = ImageDraw.Draw(img)
    font = _try_load_font(48, bold=True)
    name = (business_name or "Post")[:80]
    d.text((60, 260), name, fill=_TEXT_LIGHT, font=font)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def render_share_image_jpeg(
    business_name: str,
    caption: str,
    post_type: str = "default",
    caption_preview_chars: int = 100,
) -> bytes:
    """
    Render 1200x630 JPEG in memory (Facebook-friendly).
    """
    try:
        return _render_share_image_jpeg_inner(
            business_name, caption, post_type, caption_preview_chars
        )
    except Exception:
        logger.exception("Share image render failed; using minimal fallback JPEG")
        return _minimal_fallback_jpeg(business_name)


def _render_share_image_jpeg_inner(
    business_name: str,
    caption: str,
    post_type: str,
    caption_preview_chars: int,
) -> bytes:
    bg = _POST_TYPE_COLORS.get((post_type or "").strip(), _POST_TYPE_COLORS["default"])
    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    draw = ImageDraw.Draw(img)

    name = (business_name or "Your business").strip() or "Your business"
    preview = (caption or "").strip().replace("\r", " ").replace("\n", " ")
    if len(preview) > caption_preview_chars:
        preview = preview[: caption_preview_chars - 1].rsplit(" ", 1)[0] + "…"

    font_title = _try_load_font(52, bold=True)
    font_body = _try_load_font(26, bold=False)

    title_lines = textwrap.wrap(name, width=22)[:3]
    body_lines = textwrap.wrap(preview, width=50)[:7]

    y = 72
    max_y = HEIGHT - 40
    for line in title_lines:
        bbox = draw.textbbox((0, 0), line, font=font_title)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((WIDTH - tw) // 2, y), line, font=font_title, fill=_TEXT_LIGHT)
        y += th + 10
        if y > max_y:
            break
    y += 28
    for line in body_lines:
        if y > max_y - 30:
            break
        bbox = draw.textbbox((0, 0), line, font=font_body)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((WIDTH - tw) // 2, y), line, font=font_body, fill=_TEXT_MUTED)
        y += th + 6

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


# Backwards compatibility
def render_share_image_png(*args, **kwargs) -> bytes:
    return render_share_image_jpeg(*args, **kwargs)
