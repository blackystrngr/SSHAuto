"""One-at-a-time background jobs for slow operations (installs, list updates, repairs)."""
from __future__ import annotations

import re
import threading
import time
import uuid

from core.logger import log

ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Busy(Exception):
    pass


class JobRunner:
    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._running: str | None = None

    def start(self, title: str, fn) -> str:
        with self._lock:
            if self._running:
                raise Busy(f"'{self._jobs[self._running]['title']}' is still running")
            job_id = uuid.uuid4().hex[:10]
            self._jobs[job_id] = {"id": job_id, "title": title, "status": "running",
                                  "log": [], "result": None, "started": time.time(), "finished": None}
            self._order.append(job_id)
            while len(self._order) > 20:
                self._jobs.pop(self._order.pop(0), None)
            self._running = job_id
        threading.Thread(target=self._run, args=(job_id, fn), daemon=True, name=f"job-{job_id}").start()
        return job_id

    def _run(self, job_id: str, fn) -> None:
        job = self._jobs[job_id]

        def sink(line: str) -> None:
            if len(job["log"]) < 500:
                job["log"].append(ANSI.sub("", line))

        log.add_sink(sink)
        try:
            job["result"] = fn()
            job["status"] = "done"
        except Exception as exc:                        # reported to the UI, not raised
            job["log"].append(f"ERROR {getattr(exc, 'message', None) or exc}")
            job["status"] = "failed"
        finally:
            log.remove_sink(sink)
            job["finished"] = time.time()
            with self._lock:
                self._running = None

    def get(self, job_id: str) -> dict | None:
        job = self._jobs.get(job_id)
        return dict(job, log=list(job["log"])) if job else None

    def current(self) -> dict | None:
        with self._lock:
            return self.get(self._running) if self._running else None
