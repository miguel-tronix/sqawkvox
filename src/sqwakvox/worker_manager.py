"""Managed Celery worker subprocesses for Sqwakvox.

Historically the TUI required a *separately started* worker process::

    Terminal 1:  python -m sqwakvox.run_worker
    Terminal 2:  python -m sqwakvox

:class:`WorkerManager` removes that second terminal: when a document is
loaded the TUI asks the manager to ensure a worker exists for that
document's dedicated queue (``sqwakvox.doc<N>``), spawning
``python -m sqwakvox.run_worker`` as a child subprocess when needed.  Each
document tab therefore gets its own isolated worker — a slow Docling OCR
parse on one document never blocks agent queries on another.

Workers are terminated on :meth:`stop_all` (wired to the app's unmount and
an ``atexit`` backstop).  Set ``SQWAKVOX_MANAGED_WORKERS=0`` to disable the
whole mechanism and return to the bring-your-own-worker workflow.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: Environment variable that disables managed workers entirely.
DISABLE_ENV = "SQWAKVOX_MANAGED_WORKERS"

#: Concurrency used for spawned per-document workers.  One document's tasks
#: are mostly sequential (parse -> data store -> agent), so a small pool is
#: enough; this keeps total CPU usage sane with many tabs open.
DOC_WORKER_CONCURRENCY = 2


def managed_workers_enabled() -> bool:
    """Return True unless ``SQWAKVOX_MANAGED_WORKERS=0`` (or false/no)."""
    return os.environ.get(DISABLE_ENV, "1").strip().lower() not in ("0", "false", "no")


class WorkerManager:
    """Spawn, track, and stop one Celery worker subprocess per queue."""

    def __init__(self, log_dir: Path | None = None) -> None:
        self._workers: dict[str, subprocess.Popen[bytes]] = {}
        self._log_dir = log_dir or Path("sqwakvox_workers")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        atexit.register(self.stop_all)

    @property
    def queues(self) -> list[str]:
        """Queues with a currently-running managed worker."""
        return [q for q, proc in self._workers.items() if proc.poll() is None]

    def ensure_worker(self, queue: str) -> bool:
        """Ensure a worker consumes *queue*, spawning one if necessary.

        Returns True if a new worker was spawned, False if one was already
        running for the queue.
        """
        existing = self._workers.get(queue)
        if existing is not None and existing.poll() is None:
            return False

        # A previous worker for this queue died — drop the stale handle so we
        # can respawn a fresh one.
        self._workers.pop(queue, None)

        argv = [
            sys.executable,
            "-m",
            "sqwakvox.run_worker",
            "--queue",
            queue,
            "--loglevel",
            "WARNING",
            "--concurrency",
            str(DOC_WORKER_CONCURRENCY),
            "--no-beat",  # only one beat scheduler may run machine-wide
        ]

        log_path = self._log_dir / f"worker_{queue}.log"
        log_fh = None
        try:
            log_fh = log_path.open("ab")
        except OSError:
            logger.warning("Could not open worker log file %s", log_path)

        try:
            proc = subprocess.Popen(
                argv,
                stdout=log_fh,
                stderr=log_fh,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.error("Failed to spawn worker for queue %s: %s", queue, exc)
            if log_fh is not None:
                log_fh.close()
            return False

        self._workers[queue] = proc
        logger.info(
            "Spawned managed worker pid=%d queue=%s (log: %s)",
            proc.pid,
            queue,
            log_path,
        )
        return True

    def stop_all(self, timeout: float = 5.0) -> None:
        """Terminate every managed worker, escalating to kill on timeout."""
        for queue, proc in list(self._workers.items()):
            if proc.poll() is not None:
                del self._workers[queue]
                continue
            logger.info("Stopping managed worker pid=%d queue=%s", proc.pid, queue)
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning("Worker pid=%d ignored TERM; killing", proc.pid)
                proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=timeout)
            finally:
                self._workers.pop(queue, None)

    def wait_until_ready(self, queue: str, timeout: float = 30.0) -> bool:
        """Block until the worker for *queue* has registered with the broker.

        Uses Celery's control broadcast; returns False on timeout.  Only
        needed in tests / tooling — the TUI does not wait, it just submits
        tasks which sit in the queue until the worker comes online.
        """
        from sqwakvox.backend.celery_app import celery_app

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            proc = self._workers.get(queue)
            if proc is None or proc.poll() is not None:
                return False
            try:
                inspect = celery_app.control.inspect(timeout=1.0, destination=None)
                active = inspect.active_queues() or {}
                for _worker_name, queues in active.items():
                    if any(q["name"] == queue for q in queues):
                        return True
            except Exception as exc:  # pragma: no cover — broker hiccup
                logger.debug("Worker readiness probe failed: %s", exc)
            time.sleep(0.5)
        return False
