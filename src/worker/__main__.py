"""Entry point for the background worker process.

Usage::

    python -m src.worker

Starts the APScheduler-based scheduler and Valkey task queue consumer.
Handles graceful shutdown on SIGINT / SIGTERM.
"""
from __future__ import annotations

import asyncio
import signal
import sys

import structlog

logger = structlog.get_logger(__name__)

# Will be set by main() before signal handlers can fire.
_shutdown_event: asyncio.Event | None = None


def _signal_handler(sig: int, _frame: object) -> None:
    """Handle termination signals for graceful shutdown."""
    logger.info("shutdown_signal_received", signal=signal.Signals(sig).name)
    if _shutdown_event is not None:
        _shutdown_event.set()


async def main() -> None:
    """Start the worker with scheduler and queue consumer."""
    global _shutdown_event  # noqa: PLW0603

    from src.core.config import get_settings
    from src.monitoring.sentry_config import init_sentry
    from src.worker.queue import TaskQueue
    from src.worker.scheduler import WorkerScheduler

    _shutdown_event = asyncio.Event()

    settings = get_settings()

    # Initialize Sentry
    init_sentry(
        dsn=settings.SENTRY_DSN,
        environment="production" if not settings.DEBUG else "development",
    )

    logger.info("worker_starting", version=settings.APP_VERSION)

    # Create components
    scheduler = WorkerScheduler()
    queue = TaskQueue()

    # Start scheduler
    await scheduler.start()

    # Start queue consumer in background
    queue_task = asyncio.create_task(queue.process_loop(_shutdown_event))

    # Wait for shutdown signal
    await _shutdown_event.wait()

    # Graceful shutdown
    logger.info("worker_shutting_down")
    await scheduler.stop()
    queue_task.cancel()
    try:
        await queue_task
    except asyncio.CancelledError:
        pass

    logger.info("worker_shutdown_complete")


if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, _signal_handler)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _signal_handler)

    asyncio.run(main())
