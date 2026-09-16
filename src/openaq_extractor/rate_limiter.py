"""Rate limiting with cooldown and exponential backoff for OpenAQ API calls."""

import logging
import re
import time
from typing import Callable, TypeVar

import httpx
from openaq.shared.exceptions import (
    BadGatewayError,
    GatewayTimeoutError,
    HTTPRateLimitError,
    RateLimitError,
    ServerError,
    ServiceUnavailableError,
)

T = TypeVar("T")

logger = logging.getLogger("openaq_extractor.rate_limiter")

# Exceptions that are safe to retry (exported for sensors/measurements)
# httpx.TransportError covers connection-level failures (read/connect timeouts,
# connection resets, etc.) that aren't wrapped in an OpenAQ-specific exception
# but are just as transient as a 5xx.
RETRYABLE_EXCEPTIONS = (
    RateLimitError,
    HTTPRateLimitError,
    ServerError,
    BadGatewayError,
    ServiceUnavailableError,
    GatewayTimeoutError,
    httpx.TransportError,
)

# Regex to extract "Limit resets in N seconds" from rate limit error messages
_RESET_PATTERN = re.compile(r"limit resets in (\d+)\s*seconds", re.IGNORECASE)


def _parse_reset_seconds(message: str) -> int | None:
    """Extract reset wait time in seconds from rate limit error message."""
    if not message:
        return None
    m = _RESET_PATTERN.search(message)
    return int(m.group(1)) if m else None


def _is_rate_limit_error(e: BaseException) -> bool:
    """Return True if exception is a rate limit error (uses message-based wait)."""
    return isinstance(e, (RateLimitError, HTTPRateLimitError))


def api_call(
    fn: Callable[[], T],
    *,
    cooldown_seconds: float = 1.2,
    max_retries: int = 5,
    base_backoff_seconds: float = 60.0,
    server_error_backoff_seconds: float = 10.0,
    verbose: bool = False,
    context: str | None = None,
) -> T:
    """
    Execute an API call with cooldown and retry on rate limits / server errors.

    - Applies cooldown before each new request to avoid hitting rate limits.
    - Retries on rate limit (RateLimitError/HTTPRateLimitError) and transient
      server errors (5xx). For rate limits, parses reset time from message when
      available; otherwise uses base_backoff_seconds or server_error_backoff_seconds.
    """
    retries = 0

    while True:
        # Apply cooldown before each attempt (skip before retry—we already waited)
        if retries == 0:
            time.sleep(cooldown_seconds)

        try:
            return fn()
        except RETRYABLE_EXCEPTIONS as e:
            retries += 1
            if retries > max_retries:
                raise

            if _is_rate_limit_error(e):
                wait_seconds = _parse_reset_seconds(str(e)) or base_backoff_seconds
                msg = "Rate limited"
            else:
                wait_seconds = server_error_backoff_seconds
                msg = "Server error"

            # Exponential backoff: each successive retry waits longer
            backoff = wait_seconds * (1.5 ** (retries - 1))
            backoff = min(max(backoff, 5), 300)  # At least 5s, cap at 5 min

            log_msg = f"{msg}"
            if context:
                log_msg += f" ({context})"
            log_msg += f". Waiting {backoff:.0f}s before retry {retries}/{max_retries}..."
            logger.warning(log_msg)
            if verbose:
                print(f"  {log_msg}")

            time.sleep(backoff)
