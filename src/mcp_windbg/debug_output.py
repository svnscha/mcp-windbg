"""Bounded presentation buffers and streaming debugger log parsing."""
from __future__ import annotations

import io
import re
import time

MAX_OUTPUT_LINES = 2000
MAX_OUTPUT_CHARS = 64 * 1024
MAX_LOG_BYTES = 8 * 1024 * 1024
ROTATE_LOG_BYTES = 512 * 1024
TRUNCATED = "[output truncated at 2000 lines / 65536 characters; narrow the debugger command]"
MARKER_BASE = "COMMAND_COMPLETED_MARKER"
MARKER_LINE = re.compile(rf"{MARKER_BASE}_(?:[0-9a-f]{{32}}_)?\d+")
LOGGED_PROMPT = re.compile(r"^(?:\[.*\]\s*)?(?:\d+:[^>]*|l?kd)>")


class BoundedOutput:
    """Retain a prefix while continuing to drain the underlying transport."""

    def __init__(self):
        self.lines = []
        self.chars = 0
        self.truncated = False

    def append(self, line):
        remaining = MAX_OUTPUT_CHARS - self.chars
        if len(self.lines) >= MAX_OUTPUT_LINES or remaining <= 0:
            self.truncated = True
            return
        if len(line) + 1 > remaining:
            line = line[: max(0, remaining - 1)]
            self.truncated = True
        self.lines.append(line)
        self.chars += len(line) + 1

    def result(self):
        lines = self.lines.copy()
        while lines and lines[0] == "":
            lines.pop(0)
        while lines and lines[-1] == "":
            lines.pop()
        if self.truncated:
            lines.append(TRUNCATED)
        return lines


def output_lines(stream):
    """Bound each readline too; a single unterminated line must not grow forever."""
    while True:
        line = stream.readline(MAX_OUTPUT_CHARS)
        if not line:
            return
        yield line


def read_log_segment(path, start, marker, timeout):
    """Return bounded output and the exact UTF-16 byte boundary, or None."""
    deadline = time.monotonic() + max(2.0, timeout / 10)
    while True:
        output = BoundedOutput()
        echoed = False
        fragment = False
        try:
            with open(path, "rb") as handle:
                handle.seek(start)
                with io.TextIOWrapper(handle, encoding="utf-16-le", errors="replace", newline="") as text:
                    while True:
                        raw = text.readline(MAX_OUTPUT_CHARS)
                        if not raw:
                            break
                        complete = raw.endswith("\n")
                        content = raw.rstrip("\r\n")
                        prompt = LOGGED_PROMPT.match(content) if not fragment else None
                        if prompt and content[prompt.end():].strip() == f".echo {marker}":
                            echoed = True
                        elif not fragment and complete and content == marker and echoed:
                            return output.result(), text.tell()
                        elif not echoed and not prompt and not MARKER_LINE.fullmatch(content):
                            output.append(content)
                        fragment = not complete
                        if time.monotonic() >= deadline:
                            return None
        except OSError:
            return None
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.02)
