import io
import logging

from laserdove.logging_utils import DedupStreamHandler, setup_logging


def _reset_logging_root():
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)


def test_setup_logging_sets_level_and_format():
    _reset_logging_root()

    buf = io.StringIO()
    setup_logging("debug", stream=buf)

    logger = logging.getLogger("test_logger")
    assert logger.isEnabledFor(logging.DEBUG)
    handler = logging.root.handlers[0]
    assert isinstance(handler, DedupStreamHandler)
    assert "%(levelname)s" in handler.formatter._fmt
    logging.shutdown()


def test_repeated_messages_are_collapsed():
    _reset_logging_root()

    buf = io.StringIO()
    setup_logging("info", stream=buf)
    logger = logging.getLogger("dedup")

    logger.info("hello")
    logger.info("hello")
    logger.info("hello")
    logger.info("world")

    logging.shutdown()
    output = buf.getvalue().strip().splitlines()

    first_line = output[0]
    assert "hello" in first_line
    assert "hello.." in first_line  # dots show collapsed repeats on the same line
    assert "repeated 3 times" in first_line

    assert any("world" in line for line in output)
