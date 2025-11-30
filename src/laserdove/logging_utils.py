# logging_utils.py
import logging
import sys
from typing import IO, Optional


class DedupStreamHandler(logging.StreamHandler):
    """
    Collapse identical consecutive log messages by appending dots on the same line.
    When a new message arrives, emit a summary of how many times the previous
    message repeated and over what duration.
    """

    terminator = "\n"

    def __init__(self, stream: Optional[IO[str]] = None) -> None:
        """
        Initialize the deduping stream handler.

        Args:
            stream: Optional output stream; defaults to sys.stderr via StreamHandler.
        """
        super().__init__(stream)
        self._last_rendered: Optional[str] = None
        self._last_key: Optional[tuple[int, str, str]] = None
        self._repeat_count: int = 0
        self._repeat_start: Optional[float] = None
        self._last_time: Optional[float] = None

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record, collapsing consecutive duplicates into dots.

        Args:
            record: LogRecord to render.
        """
        try:
            msg = self.format(record)
            message_key = (record.levelno, record.name, record.getMessage())
            now = record.created
            if message_key == self._last_key:
                # Same message: append a dot instead of a new line.
                self._repeat_count += 1
                self._last_time = now
                self.stream.write(".")
                self.flush()
                return

            # New message: flush the previous sequence (if any) then start a new one.
            self._flush_sequence()
            self._last_rendered = msg
            self._last_key = message_key
            self._repeat_count = 1
            self._repeat_start = now
            self._last_time = now
            self.stream.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)

    def _flush_sequence(self) -> None:
        """Flush the currently buffered repeated message (if any)."""
        if self._last_rendered is None:
            return

        if self._repeat_count > 1 and self._repeat_start is not None and self._last_time is not None:
            duration = self._last_time - self._repeat_start
            self.stream.write(f" (repeated {self._repeat_count} times over {duration:.2f}s)")

        # Terminate the line for the previous message.
        self.stream.write(self.terminator)
        self.flush()

        # Reset tracking for the next sequence.
        self._last_rendered = None
        self._last_key = None
        self._repeat_count = 0
        self._repeat_start = None
        self._last_time = None

    def close(self) -> None:
        """Flush any pending sequence before closing the handler."""
        try:
            self._flush_sequence()
        finally:
            super().close()


def setup_logging(level: str = "INFO", stream: Optional[IO[str]] = None) -> None:
    """
    Configure root logging with the deduplicating stream handler.

    Args:
        level: Log level name (e.g., INFO, DEBUG).
        stream: Optional stream to write logs to; defaults to stdout.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    handler = DedupStreamHandler(stream or sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=log_level, handlers=[handler], force=True)
