"""Test package. Quiet two chatty third-party loggers so test output stays readable."""

import logging

logging.getLogger("asyncio").setLevel(logging.ERROR)  # slow-callback notices in debug mode
logging.getLogger("python_multipart").setLevel(logging.ERROR)  # parser notes on deliberately malformed bodies
