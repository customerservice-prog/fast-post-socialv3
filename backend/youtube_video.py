"""
FastPost Social v3 - YouTube AI Video Generator (Phase 3)

Creates animated slideshow-style videos from AI-generated images and captions,
then uploads to YouTube via the YouTube Data API v3.

Pipeline:
  1. Generate 4-6 image frames with DALL-E 3 (or Pillow fallback)
  2. Render each frame as a 1920x1080 JPEG with animated text overlays
  3. Stitch frames into an MP4 video with moviepy (or ffmpeg directly)
  4. Upload to YouTube via OAuth 2.0 (refresh token stored per account)

Requirements (add to requirements.txt):
  google-api-python-client>=2.100.0
  google-auth>=2.20.0
  google-auth-oauthlib>=1.0.0
  moviepy>=1.0.3      (or imageio[ffmpeg])
  openai>=1.0.0       (for DALL-E 3)

Environment variables (set in Railway):
  GOOGLE_CLIENT_ID          - from Google Cloud Console OAuth 2.0 credentials
  GOOGLE_CLIENT_SECRET      - from Google Cloud Console OAuth 2.0 credentials
  YOUTUBE_OAUTH_REDIRECT    - e.g. https://socialautopost.online/api/youtube/oauth/callback
"""
from __future__ import annotations

import io
import json
import logging
import os
import random
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── OAuth scopes ──────────────────────────────────────────────────────────────
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# ── Video dimensions ──────────────────────────────────────────────────────────
VID_W, VID_H = 1920, 1080
FRAME_DURATION_S = 4.0   # seconds each image frame is shown
FRAME_FPS = 24


# ── Google OAuth helpers ───────────────────────────────────────────────────────

def _google_flow(redirect_uri: str):
    """Build an OAuth flow for YouTube. Returns None if not configured."""
    client_id = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        logger.warning("[YouTube] GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set")
        return None
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri],
                }
            },
            scopes=YOUTUBE_SCOPES,
            redirect_uri=redirect_uri,
        )
        return flow
    except ImportError:
        logger.warning("[YouTube] google-auth-oauthlib not installed")
        return None


def build_oauth_authorize_url(account_id: int, redirect_uri: str, secret_key: str) -> Optional[str]:
    """Return the Google OAuth URL to redirect the user to for YouTube authorization."""
    flow = _google_flow(redirect_uri)
    if not flow:
        return None
    import hashlib, hmac
    state = f"{account_id}:{hmac.new(secret_key.encode(), f'yt_{account_id}'.encode(), hashlib.sha256).hexdigest()[:16]}"
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=state,
        prompt="consent",
    )
    return auth_url


def complete_oauth_and_store(
    db,
    account_id: int,
    code: str,
    redirect_uri: str,
) -> Tuple[bool, str]:
    """Exchange authorization code for tokens and store them for this account."""
    flow = _google_flow(redirect_uri)
    if not flow:
        return False, "Google OAuth not configured"
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes or YOUTUBE_SCOPES),
        }
        db.save_youtube_token(account_id, json.dumps(token_data))
        logger.info("[YouTube] OAuth complete for account_id=%s", account_id)
        return True, "YouTube connected"
    except Exception as e:
        logger.exception("[YouTube] OAuth token exchange failed")
        return False, str(e) or "Token exchange failed"


def _get_youtube_client(db, account_id: int):
    """Return an authenticated YouTube API client or None."""
    raw = db.get_youtube_token(account_id)
    if not raw:
        return None
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        token_data = json.loads(raw)
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes", YOUTUBE_SCOPES),
        )
        return build("youtube", "v3", credentials=creds)
    except ImportError:
        logger.warning("[YouTube] google-api-python-client not installed")
        return None
    except Exception as e:
        logger.warning("[YouTube] Could not build YouTube client: %s", e)
        return None


# ── Frame image generation ────────────────────────────────────────────────────

def _make_frame_pillow(
    text: str,
    business_name: str,
    frame_index: int,
    total_frames: int,
    bg_color: Tuple[int, int, int],
) -> bytes:
    """Create one 1920x1080 animated-style frame with Pillow (no DALL-E needed)."""
    from PIL import Image, ImageDraw, ImageFont

    # Gradient background
    img = Image.new("RGB", (VID_W, VID_H), bg_color)
    draw = ImageDraw.Draw(img)

    # Animated-style: accent shape behind text
    accent_colors = [
        (255, 200, 0), (0, 200, 255), (255, 80, 80),
        (80, 255, 160), (200, 80, 255), (255, 150, 0),
    ]
    accent = accent_colors[frame_index % len(accent_colors)]
    # Left stripe
    draw.rectangle([(0, 0), (12, VID_H)], fill=accent)
    # Bottom bar
    draw.rectangle([(0, VID_H - 8), (VID_W, VID_H)], fill=accent)

    # Progress indicator (small dot row)
    dot_y = VID_H - 40
    dot_spacing = 24
    total_dots = total_frames
    dots_x_start = (VID_W - (total_dots * dot_spacing)) // 2
    for i in range(total_dots):
        dot_x = dots_x_start + i * dot_spacing
        color = accent if i == frame_index else (80, 80, 80)
        draw.ellipse([(dot_x, dot_y), (dot_x + 12, dot_y + 12)], fill=color)

    # Fonts
    font_dir = Path(__file__).resolve().parent / "fonts"
    def try_font(size, bold=False):
        candidates = []
        if bold:
            candidates += [
                font_dir / "DejaVuSans-Bold.ttf",
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
            ]
        candidates += [
            font_dir / "DejaVuSans.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        ]
        for p in candidates:
            try:
                if p.is_file():
                    return ImageFont.truetype(str(p), size)
            except OSError:
                pass
        return ImageFont.load_default()

    font_name = try_font(56, bold=True)
    font_body = try_font(34, bold=False)
    font_frame = try_font(22, bold=False)

    # Business name at top
    draw.text((40, 40), business_name, font=font_name, fill=(255, 255, 255))

    # Frame counter
    frame_label = f"Tip {frame_index + 1} of {total_frames}"
    draw.text((VID_W - 220, 50), frame_label, font=font_frame, fill=(180, 180, 180))

    # Main text centered
    lines = textwrap.wrap(text, width=55)[:8]
    y = 200
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_body)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((VID_W - tw) // 2, y), line, font=font_body, fill=(240, 240, 240))
        y += th + 16

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _make_frame_dall_e(
    image_prompt: str,
    text: str,
    business_name: str,
    frame_index: int,
    total_frames: int,
) -> Optional[bytes]:
    """Generate a DALL-E 3 frame and overlay text. Returns None if unavailable."""
    import urllib.request
    client_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not client_key:
        return None
    try:
        import openai
        client = openai.OpenAI(api_key=client_key)
        enhanced_prompt = (
            f"{image_prompt}. Animated, modern illustration style suitable for a YouTube video. "
            "Bright, engaging, professional. No text in the image."
        )
        resp = client.images.generate(
            model="dall-e-3",
            prompt=enhanced_prompt[:4000],
            size="1792x1024",
            quality="standard",
            n=1,
        )
        url = resp.data[0].url
        with urllib.request.urlopen(url, timeout=30) as r:
            img_bytes = r.read()

        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        # Resize to 1920x1080
        img = img.resize((VID_W, VID_H), Image.LANCZOS)
        # Add semi-transparent text overlay at bottom
        overlay = Image.new("RGBA", (VID_W, VID_H), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        bar_h = 200
        for y in range(bar_h):
            alpha = int(200 * (1 - y / bar_h))
            d.rectangle([(0, VID_H - bar_h + y), (VID_W, VID_H - bar_h + y + 1)], fill=(0, 0, 0, alpha))
        img_rgba = img.convert("RGBA")
        img_rgba = Image.alpha_composite(img_rgba, overlay)
        img = img_rgba.convert("RGB")

        draw = ImageDraw.Draw(img)
        font_dir = Path(__file__).resolve().parent / "fonts"
        def try_font(size, bold=False):
            candidates = []
            if bold:
                candidates.append(font_dir / "DejaVuSans-Bold.ttf")
            candidates.append(font_dir / "DejaVuSans.ttf")
            for p in candidates:
                try:
                    if p.is_file():
                        return ImageFont.truetype(str(p), size)
                except OSError:
                    pass
            return ImageFont.load_default()

        font_name = try_font(42, bold=True)
        font_body = try_font(28)
        draw.text((40, VID_H - 170), business_name, font=font_name, fill=(255, 255, 255))
        lines = textwrap.wrap(text, width=80)[:2]
        y = VID_H - 110
        for line in lines:
            draw.text((40, y), line, font=font_body, fill=(220, 220, 220))
            y += 36

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
    except Exception as e:
        logger.warning("[YouTube] DALL-E 3 frame failed: %s", str(e)[:200])
        return None


# ── Video stitching ────────────────────────────────────────────────────────────

BG_COLORS = [
    (18, 30, 60), (15, 55, 40), (55, 18, 60),
    (30, 30, 30), (60, 35, 10), (10, 45, 60),
]


def create_animated_video(
    business_name: str,
    caption: str,
    image_prompts: List[str],
    post_type: str = "default",
    use_dall_e: bool = True,
) -> Optional[bytes]:
    """
    Create an animated MP4 video from a caption split into frames.

    Steps:
    1. Split caption into 4-6 text segments (one per frame)
    2. Generate each frame as a JPEG (DALL-E 3 or Pillow)
    3. Stitch with moviepy into an MP4
    Returns MP4 bytes or None on failure.
    """
    # Split caption into ~4 segments for slides
    sentences = [s.strip() for s in caption.replace("\n\n", "\n").split("\n") if s.strip()]
    # Merge short sentences, cap at 6 slides
    segments = _merge_into_segments(sentences, max_segments=min(6, max(4, len(image_prompts))))
    total = len(segments)

    logger.info("[YouTube] Creating animated video: %d frames for %s", total, business_name)

    # Generate frames
    frame_paths = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        for i, seg in enumerate(segments):
            prompt = image_prompts[i] if i < len(image_prompts) else image_prompts[-1] if image_prompts else ""
            bg = BG_COLORS[i % len(BG_COLORS)]

            frame_bytes = None
            if use_dall_e and prompt:
                frame_bytes = _make_frame_dall_e(prompt, seg, business_name, i, total)

            if frame_bytes is None:
                frame_bytes = _make_frame_pillow(seg, business_name, i, total, bg)

            frame_path = tmp / f"frame_{i:03d}.jpg"
            frame_path.write_bytes(frame_bytes)
            frame_paths.append(str(frame_path))
            logger.info("[YouTube] Frame %d/%d saved (%d bytes)", i + 1, total, len(frame_bytes))

        # Stitch with moviepy
        out_path = tmp / "video.mp4"
        video_bytes = _stitch_frames_to_mp4(frame_paths, str(out_path))

    return video_bytes


def _merge_into_segments(sentences: List[str], max_segments: int) -> List[str]:
    """Merge sentence list into at most max_segments text blocks."""
    if not sentences:
        return ["Watch this space for updates!"]
    if len(sentences) <= max_segments:
        return sentences
    # Group sentences
    group_size = (len(sentences) + max_segments - 1) // max_segments
    groups = []
    for i in range(0, len(sentences), group_size):
        chunk = " ".join(sentences[i : i + group_size])
        groups.append(chunk[:280])
    return groups[:max_segments]


def _stitch_frames_to_mp4(frame_paths: List[str], out_path: str) -> Optional[bytes]:
    """Stitch JPEG frames into MP4. Tries moviepy first, then ffmpeg subprocess."""
    try:
        return _stitch_with_moviepy(frame_paths, out_path)
    except Exception as e:
        logger.warning("[YouTube] moviepy stitch failed (%s), trying ffmpeg", str(e)[:200])
    try:
        return _stitch_with_ffmpeg(frame_paths, out_path)
    except Exception as e:
        logger.error("[YouTube] Both stitching methods failed: %s", str(e)[:200])
        return None


def _stitch_with_moviepy(frame_paths: List[str], out_path: str) -> bytes:
    from moviepy.editor import ImageSequenceClip
    clip = ImageSequenceClip(frame_paths, durations=[FRAME_DURATION_S] * len(frame_paths))
    clip.write_videofile(
        out_path,
        fps=FRAME_FPS,
        codec="libx264",
        audio=False,
        verbose=False,
        logger=None,
    )
    clip.close()
    return Path(out_path).read_bytes()


def _stitch_with_ffmpeg(frame_paths: List[str], out_path: str) -> bytes:
    import subprocess, tempfile as tf
    from pathlib import Path as P
    # Write concat list
    tmp_list = str(P(out_path).parent / "concat.txt")
    with open(tmp_list, "w") as f:
        for fp in frame_paths:
            f.write(f"file '{fp}'\n")
            f.write(f"duration {FRAME_DURATION_S}\n")
        # Repeat last frame (ffmpeg concat demuxer requirement)
        f.write(f"file '{frame_paths[-1]}'\n")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", tmp_list,
        "-vf", f"scale={VID_W}:{VID_H}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(FRAME_FPS),
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    return Path(out_path).read_bytes()


# ── YouTube upload ─────────────────────────────────────────────────────────────

def upload_video_to_youtube(
    db,
    account_id: int,
    video_bytes: bytes,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
    category_id: str = "22",  # "People & Blogs"
    privacy: str = "public",
) -> Tuple[bool, str]:
    """
    Upload an MP4 to YouTube for the given account.
    Returns (success, video_id_or_error_message).
    """
    yt = _get_youtube_client(db, account_id)
    if yt is None:
        return False, "YouTube not connected for this account. Connect under Accounts."

    try:
        import googleapiclient.http as ghttp
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(video_bytes)
            tmp_path = f.name

        try:
            body = {
                "snippet": {
                    "title": title[:100],
                    "description": description[:5000],
                    "tags": (tags or [])[:500],
                    "categoryId": category_id,
                },
                "status": {"privacyStatus": privacy},
            }
            media = ghttp.MediaFileUpload(tmp_path, mimetype="video/mp4", resumable=True)
            req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                status, response = req.next_chunk()
                if status:
                    logger.info("[YouTube] Upload progress: %.0f%%", status.progress() * 100)
            vid_id = response.get("id", "")
            logger.info("[YouTube] Uploaded video_id=%s for account_id=%s", vid_id, account_id)
            return True, vid_id
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        logger.exception("[YouTube] Upload failed")
        return False, str(e) or "Upload failed"


def youtube_configured() -> bool:
    """True if Google OAuth credentials are present."""
    return bool(
        (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
        and (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    )
