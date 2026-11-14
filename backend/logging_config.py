"""Shared, credential-safe logging setup for the small service processes."""
import logging
import traceback


def configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log_exception(logger, context, error):
    # Stack frames diagnose code faults without exposing exception messages or provider response bodies.
    frames = traceback.extract_tb(error.__traceback__) if error.__traceback__ else ()
    trace = "".join(traceback.format_list(frames)) or "(no traceback available)\n"
    logger.error("%s (%s)\n%s", context, type(error).__name__, trace.rstrip())
