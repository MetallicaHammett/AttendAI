"""
Camera source helpers: credential masking and validation of what a camera record may
point at. Kept free of OpenCV/model imports so schemas and routers can use it cheaply.
"""
import os
import re
from typing import Any, Optional, Tuple

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# user:password is everything up to the LAST "@" before the host. A password may itself contain
# "@" (FFmpeg splits at the last one too); stopping at the first "@" would leave the rest of the
# password visible in "masked" output.
_CREDENTIALS_IN_URL = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<userinfo>[^/\s]+)@")
MASK = "***"
STREAM_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://")
VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".m4v", ".webm", ".mpg", ".mpeg")


def mask_source(source: Any) -> str:
    """Camera source safe for logs, errors and API responses: the user:password part of
    a stream URL (rtsp://user:pass@host/...) is replaced by '***'."""
    return _CREDENTIALS_IN_URL.sub(lambda m: f"{m.group('scheme')}{MASK}@", str(source))


def has_masked_credentials(source: str) -> bool:
    return f"://{MASK}@" in source


def restore_credentials(submitted: str, stored: str) -> str:
    """A client that edits a camera only ever saw the masked URL. If it sends the masked
    URL back unchanged (apart from the host/path it edited), keep the stored credentials."""
    if not has_masked_credentials(submitted):
        return submitted
    match = _CREDENTIALS_IN_URL.search(stored or "")
    if match is None:
        raise ValueError("The source contains '***' credentials but no stored credentials exist; "
                         "enter the full stream URL")
    return submitted.replace(f"://{MASK}@", f"://{match.group('userinfo')}@", 1)


def video_files_dir(configured: str) -> str:
    path = configured if os.path.isabs(configured) else os.path.join(BACKEND_DIR, configured)
    return os.path.realpath(path)


def validate_source(source_type: str, source: str, video_dir: str) -> Tuple[Optional[str], str]:
    """(error message or None, normalised source). Webcams are device indexes; streams
    are rtsp/http(s) URLs; video files must be inside the configured video folder."""
    source = (source or "").strip()
    if source_type == "webcam":
        if not source.isdigit() or int(source) > 63:
            return "A webcam source is a device index such as 0 or 1", source
        return None, str(int(source))
    if source_type == "rtsp":
        if not source.lower().startswith(STREAM_SCHEMES):
            return "A stream source must start with rtsp://, rtsps://, http:// or https://", source
        if any(ch.isspace() for ch in source):
            return "A stream URL must not contain spaces", source
        return None, source
    if source_type == "video":
        if "://" in source:
            return "A video file source is a file path; use source type 'rtsp' for stream URLs", source
        if not source.lower().endswith(VIDEO_EXTENSIONS):
            return f"Unsupported video file type (allowed: {', '.join(VIDEO_EXTENSIONS)})", source
        base = video_files_dir(video_dir)
        candidate = source if os.path.isabs(source) else os.path.join(base, source)
        resolved = os.path.realpath(candidate)
        if os.path.commonpath([resolved, base]) != base:
            return f"Video files must be inside the video folder ({base})", source
        if not os.path.isfile(resolved):
            return f"Video file not found in the video folder: {os.path.relpath(resolved, base)}", source
        # Stored relative to the video folder: the server's directory layout is not exposed
        return None, os.path.relpath(resolved, base).replace(os.sep, "/")
    return f"Unknown source type {source_type!r}", source


def resolve_video_source(source: str, video_dir: str) -> str:
    """Absolute path to open for a video-file camera (relative sources live in the video folder)"""
    return source if os.path.isabs(source) else os.path.join(video_files_dir(video_dir), source)


def feed_kind_for(source_type: Optional[str]) -> str:
    return "recorded" if source_type == "video" else "live"
