"""Download jobs: the queue and history the SABnzbd shim reports on.

Each job pulls one episode's HLS stream with yt-dlp into ``incomplete_dir``,
then moves the finished folder into ``complete_dir/<category>/`` for Sonarr to
import. State is persisted so a restart mid-tournament does not orphan whatever
Sonarr has already grabbed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, field
from enum import Enum

from .config import Config
from .nhk import Episode, NhkClient

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "Queued"
    DOWNLOADING = "Downloading"
    COMPLETED = "Completed"
    FAILED = "Failed"

    @property
    def terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED)


@dataclass
class Job:
    nzo_id: str
    nhk_id: str
    name: str
    category: str
    total_bytes: int
    status: JobStatus = JobStatus.QUEUED
    downloaded_bytes: int = 0
    storage: str = ""
    fail_message: str = ""
    added_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    @property
    def percentage(self) -> int:
        if not self.total_bytes:
            return 0
        return min(100, int(self.downloaded_bytes * 100 / self.total_bytes))

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.total_bytes - self.downloaded_bytes)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Job:
        data = dict(data)
        data["status"] = JobStatus(data.get("status", JobStatus.QUEUED.value))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class DownloadManager:
    """Owns the job table and a bounded pool of yt-dlp workers."""

    def __init__(self, config: Config, client: NhkClient):
        self._config = config
        self._client = client
        self._jobs: dict[str, Job] = {}
        self._pending: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._lock = asyncio.Lock()
        self._load_state()

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        self._config.incomplete_dir.mkdir(parents=True, exist_ok=True)
        self._config.category_dir.mkdir(parents=True, exist_ok=True)

        # Anything mid-flight when we stopped never finished; re-queue it.
        for job in self._jobs.values():
            if not job.status.terminal:
                job.status = JobStatus.QUEUED
                job.downloaded_bytes = 0
                self._pending.put_nowait(job.nzo_id)

        for index in range(max(1, self._config.max_concurrent)):
            self._workers.append(
                asyncio.create_task(self._worker(), name=f"sumo-download-{index}")
            )

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        self._save_state()

    # -- job table --------------------------------------------------------

    def get(self, nzo_id: str) -> Job | None:
        return self._jobs.get(nzo_id)

    def find_by_episode(self, nhk_id: str) -> Job | None:
        for job in self._jobs.values():
            if job.nhk_id == nhk_id:
                return job
        return None

    @property
    def queue(self) -> list[Job]:
        jobs = [j for j in self._jobs.values() if not j.status.terminal]
        return sorted(jobs, key=lambda j: j.added_at)

    @property
    def history(self) -> list[Job]:
        jobs = [j for j in self._jobs.values() if j.status.terminal]
        return sorted(jobs, key=lambda j: j.completed_at or j.added_at, reverse=True)

    async def add(self, episode: Episode, name: str, category: str | None = None) -> Job:
        """Queue an episode, or return the existing job if it is already known."""
        async with self._lock:
            existing = self.find_by_episode(episode.nhk_id)
            if existing is not None and existing.status is not JobStatus.FAILED:
                log.info("episode %s already known as %s", episode.nhk_id, existing.nzo_id)
                return existing

            job = Job(
                nzo_id=f"SABnzbd_nzo_{episode.nhk_id}",
                nhk_id=episode.nhk_id,
                name=name,
                category=category or self._config.category,
                total_bytes=episode.size_bytes,
            )
            self._jobs[job.nzo_id] = job
            self._save_state()

        await self._pending.put(job.nzo_id)
        log.info("queued %s (%s)", job.name, job.nzo_id)
        return job

    def remove(self, nzo_id: str, delete_files: bool = False) -> bool:
        job = self._jobs.pop(nzo_id, None)
        if job is None:
            return False
        if delete_files and job.storage:
            shutil.rmtree(job.storage, ignore_errors=True)
        self._save_state()
        log.info("removed %s (delete_files=%s)", nzo_id, delete_files)
        return True

    # -- workers ----------------------------------------------------------

    async def _worker(self) -> None:
        while True:
            nzo_id = await self._pending.get()
            job = self._jobs.get(nzo_id)
            if job is None or job.status.terminal:
                continue
            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                job.status = JobStatus.QUEUED
                self._save_state()
                raise
            except Exception as exc:  # noqa: BLE001 - a failed job must not kill the worker
                log.exception("download failed for %s", job.name)
                job.status = JobStatus.FAILED
                job.fail_message = str(exc)[:500]
                job.completed_at = time.time()
                self._save_state()

    async def _run_job(self, job: Job) -> None:
        from .ytdlp import download  # imported lazily so tests need no yt-dlp

        job.status = JobStatus.DOWNLOADING
        self._save_state()

        work_dir = self._config.incomplete_dir / job.nzo_id
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)

        def on_progress(downloaded: int, total: int | None) -> None:
            job.downloaded_bytes = downloaded
            # total_bytes starts as an estimate from the episode duration. Only
            # ever grow it, so the reported percentage never jumps backwards.
            job.total_bytes = max(job.total_bytes, total or 0, downloaded)

        stream_url = await self._stream_url(job)
        await asyncio.to_thread(
            download,
            stream_url,
            work_dir,
            job.name,
            self._config.video_format,
            on_progress,
        )

        destination = self._config.complete_dir / job.category / job.name
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work_dir), str(destination))

        job.storage = str(destination)
        job.status = JobStatus.COMPLETED
        job.downloaded_bytes = job.total_bytes
        job.completed_at = time.time()
        self._save_state()
        log.info("completed %s -> %s", job.name, destination)

    async def _stream_url(self, job: Job) -> str:
        """Re-resolve the HLS URL at download time.

        NHK's playlist URLs are tied to the publishing window, so the one seen
        when the release was advertised may already be stale by the time Sonarr
        gets round to grabbing it.
        """
        episode = await self._client.get(job.nhk_id)
        if episode is None:
            raise RuntimeError(
                f"episode {job.nhk_id} is no longer offered by NHK "
                "(episodes expire about two weeks after airing)"
            )
        return episode.stream_url

    # -- persistence ------------------------------------------------------

    def _load_state(self) -> None:
        path = self._config.state_file
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            log.warning("could not read state file %s: %s", path, exc)
            return
        for entry in payload.get("jobs", []):
            try:
                job = Job.from_dict(entry)
            except (TypeError, ValueError) as exc:
                log.warning("skipping unreadable job entry: %s", exc)
                continue
            self._jobs[job.nzo_id] = job
        log.info("restored %d jobs from %s", len(self._jobs), path)

    def _save_state(self) -> None:
        path = self._config.state_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"jobs": [j.to_dict() for j in self._jobs.values()]}, indent=1)
            )
            temporary.replace(path)
        except OSError as exc:
            log.warning("could not persist state to %s: %s", path, exc)
