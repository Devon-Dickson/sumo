"""Thin yt-dlp wrapper.

Kept in its own module with a blocking signature so :mod:`downloader` can push
it onto a worker thread, and so the rest of the package imports cleanly without
yt-dlp installed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, "int | None"], None]


def download(
    stream_url: str,
    work_dir: Path,
    name: str,
    video_format: str,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Fetch one HLS stream into ``work_dir`` and return the resulting file.

    NHK serves video and audio as separate renditions, so the selected formats
    are merged — this needs ffmpeg on PATH.
    """
    import yt_dlp

    # Video and audio arrive as separate HLS renditions, and yt-dlp restarts its
    # byte counter for each. Carry a running base across them so progress climbs
    # monotonically instead of resetting partway through.
    finished_bytes = 0

    def hook(status: dict) -> None:
        nonlocal finished_bytes
        if on_progress is None:
            return
        state = status.get("status")
        downloaded = int(status.get("downloaded_bytes") or 0)
        if state == "downloading":
            on_progress(finished_bytes + downloaded, None)
        elif state == "finished":
            finished_bytes += downloaded
            on_progress(finished_bytes, None)

    options = {
        "format": video_format,
        "merge_output_format": "mkv",
        "outtmpl": str(work_dir / f"{name}.%(ext)s"),
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "logger": log,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([stream_url])

    produced = sorted(p for p in work_dir.iterdir() if p.is_file())
    if not produced:
        raise RuntimeError(f"yt-dlp produced no output for {name}")
    return max(produced, key=lambda p: p.stat().st_size)
