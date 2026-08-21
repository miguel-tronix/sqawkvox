"""Entry-point script for running the Sqwakvox Celery worker.

Usage::

    python -m sqwakvox.run_worker            # default: info logging
    python -m sqwakvox.run_worker --loglevel=debug
    python -m sqwakvox.run_worker --queue sqwakvox.doc0 --no-beat --concurrency 1

The worker imports the :mod:`sqwakvox.backend.tasks` module (auto-registered
via ``celery_app.conf.include``) which in turn imports
:class:`~sqwakvox.controller.AppController` for real document parsing,
cross-validation, and agent execution.

The TUI normally spawns and manages its own workers automatically (one per
document tab — see :mod:`sqwakvox.worker_manager`).  Running this module by
hand is still supported for debugging or for a single shared worker; set
``SQWAKVOX_MANAGED_WORKERS=0`` when launching the TUI to disable the managed
workers and route everything to the queue consumed here::

    Terminal 1:  python -m sqwakvox.run_worker
    Terminal 2:  SQWAKVOX_MANAGED_WORKERS=0 python -m sqwakvox
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqwakvox.backend.celery_app import celery_app

logger = logging.getLogger("sqwakvox")

DEFAULT_QUEUE = "sqwakvox"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Sqwakvox Celery worker")
    parser.add_argument(
        "--loglevel",
        default="INFO",
        help="Celery worker log level (default: INFO)",
    )
    parser.add_argument(
        "--queue",
        default=DEFAULT_QUEUE,
        help=f"Celery queue to consume (default: {DEFAULT_QUEUE})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Number of child worker processes (default: Celery's own default)",
    )
    parser.add_argument(
        "--beat",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run the beat scheduler alongside the worker "
        "(only one process may run beat; managed per-document workers pass --no-beat).",
    )
    args = parser.parse_args()

    # Configure logging for the worker process.
    logging.basicConfig(
        level=getattr(logging, args.loglevel.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Ensure telemetry is set up in the worker (for tracing spans).
    from sqwakvox.telemetry import setup_telemetry

    setup_telemetry()

    argv = [
        "worker",
        "-l",
        args.loglevel,
        "-Q",
        args.queue,  # only consume the requested queue
    ]
    if args.beat:
        # Beat scheduler — needed for periodic tasks (future cron-style jobs).
        # Only one process on the machine may run it at a time.
        argv.append("-B")
    if args.concurrency is not None:
        argv.extend(["--concurrency", str(args.concurrency)])
    try:
        celery_app.worker_main(argv)
    except KeyboardInterrupt:
        logger.info("Worker shutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
