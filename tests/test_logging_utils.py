import logging

from laserdove.logging_utils import setup_logging


def test_setup_logging_sets_level_and_format(monkeypatch):
    # Reset root handlers to avoid interference.
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    setup_logging("debug")

    logger = logging.getLogger("test_logger")
    assert logger.isEnabledFor(logging.DEBUG)
    # Ensure formatter roughly matches expected pattern.
    handler = logging.root.handlers[0]
    assert "%(levelname)s" in handler.formatter._fmt
