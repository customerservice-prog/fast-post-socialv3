"""
FastPost Social v3 - Image Generator

Phase 2: AI-powered image generation with DALL-E 3.
Falls back to Pillow text-card if OPENAI_API_KEY is not set or DALL-E fails.

Usage:
  render_share_image_jpeg(business_name, caption, post_type)
    -> bytes (JPEG, 1200x630 for Facebook/Instagram)

  generate_ai_image_jpeg(image_prompt, post_type)
    -> bytes (JPEG, 1024x1024 from DALL-E 3, cropped to 1200x630)
"""
from __future__ import annotations

import io
import logging
import os
import textwrap
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

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


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# DALL-E 3 AI image generation (Phase 2)
# ---------------------------------------------------------------------------

def _openai_client():
    """Lazy-import OpenAI client — returns None if not installed or key missing."""
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        import openai
        return openai.OpenAI(api_key=api_key)
    except ImportError:
        logger.warning("[PostImage] openai package not installed — falling back to Pillow image")
        return None


def generate_ai_image_jpeg(
    image_prompt: str,
    post_type: str = "default",
    size: str = "1024x1024",
) -> Optional[bytes]:
    """
    Generate an AI image with DALL-E 3 from the given prompt.
    Returns JPEG bytes (cropped/resized to 1200x630) or None if unavailable/failed.
    The prompt is automatically enhanced to produce a professional, animated-style image
    suitable for Facebook/Instagram business posts.
    """
    client = _openai_client()
    if client is None:
        return None

    # Enhance the prompt for better social media images
    style_map = {
        "morning_promo": "vibrant, energetic, warm golden morning light, professional business photography style",
        "afternoon_tip": "clean, modern, bright professional infographic style, trustworthy and helpful",
        "evening_proof": "warm, authentic, community-feel photography, soft evening light, genuine and relatable",
    }
    style = style_map.get(post_type, "professional, clean, modern business photography")
    full_prompt = (
        f"{image_prompt}. Style: {style}. "
        "Visually striking for social media. No text or watermarks in the image. "
        "High quality, photorealistic or stylized illustration, suitable for a business marketing post."
    )

    try:
        logger.info("[PostImage] Generating DALL-E 3 image for post_type=%s", post_type)
        response = client.images.generate(
            model="dall-e-3",
            prompt=full_prompt[:4000],
            size=size,
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
        logger.info("[PostImage] DALL-E 3 image URL: %s...", image_url[:80])

        # Download the image
        with urllib.request.urlopen(image_url, timeout=30) as resp:
            img_bytes = resp.read()

        # Resize/crop to 1200x630
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img = _resize_crop_to(img, WIDTH, HEIGHT)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90, optimize=True)
        logger.info("[PostImage] DALL-E 3 image ready (%d bytes)", buf.tell())
        return buf.getvalue()

    except Exception as e:
        logger.warning("[PostImage] DALL-E 3 failed (%s) — falling back to Pillow", str(e)[:200])
        return None


def _resize_crop_to(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Resize then center-crop to exact dimensions."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


# ---------------------------------------------------------------------------
# Public API: render_share_image_jpeg (with AI image support)
# ---------------------------------------------------------------------------

def render_share_image_jpeg(
    business_name: str,
    caption: str,
    post_type: str = "default",
    caption_preview_chars: int = 100,
    image_prompt: Optional[str] = None,
    use_ai_image: bool = True,
) -> bytes:
    """
    Render 1200x630 JPEG in memory (Facebook/Instagram-friendly).

    If OPENAI_API_KEY is set and use_ai_image=True, tries DALL-E 3 first.
    Falls back to Pillow text-card on any error.
    """
    # Try AI image first
    if use_ai_image and image_prompt:
        ai_bytes = generate_ai_image_jpeg(image_prompt, post_type)
        if ai_bytes:
            # Overlay business name + brief caption on the AI image
            try:
                return _overlay_text_on_ai_image(
                    ai_bytes, business_name, caption, caption_preview_chars
                )
            except Exception as e:
                logger.warning("[PostImage] Text overlay failed: %s — using raw AI image", e)
                return ai_bytes

    # Pillow fallback
    try:
        return _render_share_image_jpeg_inner(
            business_name, caption, post_type, caption_preview_chars
        )
    except Exception:
        logger.exception("Share image render failed; using minimal fallback JPEG")
        return _minimal_fallback_jpeg(business_name)


def _overlay_text_on_ai_image(
    img_bytes: bytes,
    business_name: str,
    caption: str,
    caption_preview_chars: int,
) -> bytes:
    """Add a semi-transparent bottom bar with business name and caption preview over an AI image."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")

    # Dark gradient overlay at bottom (20% height)
    bar_h = int(img.height * 0.22)
    overlay = Image.new("RGBA", (img.width, img.height), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    for y in range(bar_h):
        alpha = int(190 * (1 - y / bar_h))
        draw_ov.rectangle(
            [(0, img.height - bar_h + y), (img.width, img.height - bar_h + y + 1)],
            fill=(0, 0, 0, alpha),
        )
    img = Image.alpha_composite(img, overlay).convert("RGB")

    draw = ImageDraw.Draw(img)
    font_name = _try_load_font(36, bold=True)
    font_cap = _try_load_font(20, bold=False)

    name = (business_name or "").strip()[:60]
    preview = (caption or "").strip().replace("\r", " ").replace("\n", " ")
    if len(preview) > caption_preview_chars:
        preview = preview[: caption_preview_chars - 1].rsplit(" ", 1)[0] + "\u2026"

    pad = 20
    y_start = img.height - bar_h + 12
    draw.text((pad, y_start), name, font=font_name, fill=(255, 255, 255))
    y_start += 42
    draw.text((pad, y_start), preview[:120], font=font_cap, fill=(220, 220, 220))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pillow-only text card (fallback)
# ---------------------------------------------------------------------------

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
        preview = preview[: caption_preview_chars - 1].rsplit(" ", 1)[0] + "\u2026"

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
