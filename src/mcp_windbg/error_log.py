"""Asynchronous, rotating error diagnostics, never on the MCP stdout channel."""
from __future__ import annotations

import atexit
import logging
import logging.handlers
import queue
import sys
import threading
from pathlib import Path

_lock = threading.Lock()
_logger = logging.getLogger("mcp_windbg.errors")
_logger.propagate = False
_listener = None


def _stop_listener():
    if _listener is not None:
        _listener.stop()


# Register before the server's atexit cleanup so its errors are drained last.
atexit.register(_stop_listener)


def log_error(message, *args, exc_info=False):
    global _listener
    with _lock:
        if not _logger.handlers:
            records = queue.Queue(maxsize=1024)
            try:
                directory = Path(__file__).resolve().parent / "logs"
                directory.mkdir(exist_ok=True)
                sink = logging.handlers.RotatingFileHandler(
                    directory / "errors.log", maxBytes=1024 * 1024,
                    backupCount=3, encoding="utf-8",
                )
            except OSError:
                sink = logging.StreamHandler(sys.stderr)
            sink.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            listener = logging.handlers.QueueListener(records, sink)
            # Drain before the exit sentinel even if the bounded queue is full.
            listener.enqueue_sentinel = lambda: records.put(None)
            listener.start()
            _listener = listener
            # ponytail: saturated diagnostics fall back to QueueHandler's stderr
            # error report; use a larger queue only if measured error volume needs it.
            _logger.addHandler(logging.handlers.QueueHandler(records))
            _logger.setLevel(logging.ERROR)
    _logger.error(message, *args, exc_info=exc_info)
