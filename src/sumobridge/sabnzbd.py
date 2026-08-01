"""SABnzbd API emulation.

Sonarr has no notion of "run yt-dlp on this URL", so the download side pretends
to be SABnzbd — the simplest of Sonarr's download-client protocols, and a plain
JSON one. Sonarr adds an NZB, polls the queue, then reads ``storage`` out of the
history entry to know what to import.

Only the subset Sonarr actually calls is implemented.
"""

from __future__ import annotations

import time

from .config import Config
from .downloader import DownloadManager, Job

#: Reported to Sonarr. Sonarr refuses some development builds, so a plain
#: released version number is used.
SABNZBD_VERSION = "4.3.3"


def _timeleft(job: Job) -> str:
    """SABnzbd formats remaining time as ``H:MM:SS``."""
    elapsed = max(1.0, time.time() - job.added_at)
    if job.downloaded_bytes <= 0:
        return "0:00:00"
    rate = job.downloaded_bytes / elapsed
    seconds = int(job.remaining_bytes / rate) if rate > 0 else 0
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _mb(value: int) -> str:
    return f"{value / (1024 * 1024):.2f}"


def version_response() -> dict:
    return {"version": SABNZBD_VERSION}


def config_response(config: Config) -> dict:
    """``mode=get_config``.

    Sonarr reads ``misc.complete_dir`` and the category list from here to
    validate its settings, and warns if the completed directory looks wrong.
    """
    return {
        "config": {
            "misc": {
                "complete_dir": str(config.complete_dir),
                "download_dir": str(config.incomplete_dir),
                "pre_check": False,
                "history_retention": "",
                "history_retention_option": "all",
                "history_retention_number": 0,
                "enable_tv_sorting": False,
                "enable_movie_sorting": False,
                "enable_date_sorting": False,
                "dirscan_dir": "",
            },
            "categories": [
                {
                    "name": "*",
                    "order": 0,
                    "dir": "",
                    "priority": 0,
                    "script": "None",
                    "pp": "3",
                },
                {
                    "name": config.category,
                    "order": 1,
                    "dir": config.category,
                    "priority": 0,
                    "script": "None",
                    "pp": "3",
                },
            ],
            "servers": [],
        }
    }


def queue_response(manager: DownloadManager) -> dict:
    slots = []
    for index, job in enumerate(manager.queue):
        slots.append(
            {
                "index": index,
                "nzo_id": job.nzo_id,
                "unpackopts": "3",
                "priority": "Normal",
                "script": "None",
                "filename": job.name,
                "cat": job.category,
                "mbleft": _mb(job.remaining_bytes),
                "mb": _mb(job.total_bytes),
                "size": f"{job.total_bytes / (1024 * 1024 * 1024):.2f} GB",
                "sizeleft": f"{job.remaining_bytes / (1024 * 1024 * 1024):.2f} GB",
                "percentage": str(job.percentage),
                "status": job.status.value,
                "timeleft": _timeleft(job),
            }
        )
    return {
        "queue": {
            "paused": False,
            "speedlimit": "0",
            "speedlimit_abs": "",
            "have_warnings": "0",
            "diskspacetotal1": "0",
            "diskspace1": "0",
            "noofslots": len(slots),
            "slots": slots,
        }
    }


def history_response(manager: DownloadManager) -> dict:
    slots = []
    for job in manager.history:
        slots.append(
            {
                "id": job.nzo_id,
                "nzo_id": job.nzo_id,
                "name": job.name,
                "nzb_name": f"{job.name}.nzb",
                "category": job.category,
                "size": job.total_bytes,
                "bytes": job.total_bytes,
                "storage": job.storage,
                "path": job.storage,
                "status": job.status.value,
                "fail_message": job.fail_message,
                "download_time": int((job.completed_at or 0) - job.added_at),
                "postproc_time": 0,
                "completed": int(job.completed_at or 0),
                "script": "None",
                "script_line": "",
            }
        )
    return {"history": {"noofslots": len(slots), "slots": slots}}
