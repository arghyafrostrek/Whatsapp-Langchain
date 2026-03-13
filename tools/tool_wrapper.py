"""
Tool execution wrapper with retry and timeout support.
Prevents tool failures from crashing the assistant.
"""

import time
from typing import Any, Callable, Dict
from logger_config import logger


def execute_with_retry(
    func: Callable,
    *args: Any,
    retries: int = 2,
    timeout: int = 10,
    **kwargs: Any,
) -> Any:
    """
    Execute a function with retry logic and timeout.

    Args:
        func: The function to execute.
        *args: Positional arguments for the function.
        retries: Number of retry attempts (default 2).
        timeout: Not enforced as a hard deadline, but logged for awareness.
        **kwargs: Keyword arguments for the function.

    Returns:
        The function's return value on success, or a structured error dict on failure.
    """

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            start = time.time()
            result = func(*args, **kwargs)
            elapsed = time.time() - start

            logger.info(
                "tool_call_success func=%s attempt=%s/%s elapsed=%.2fs",
                func.__name__, attempt, retries, elapsed,
            )

            if elapsed > timeout:
                logger.warning(
                    "tool_call_slow func=%s elapsed=%.2fs timeout=%ss",
                    func.__name__, elapsed, timeout,
                )

            return result

        except Exception as e:
            last_error = e
            logger.warning(
                "tool_call_failed func=%s attempt=%s/%s error=%s",
                func.__name__, attempt, retries, e,
            )

            if attempt < retries:
                time.sleep(1)  # Brief backoff before retry

    # All retries exhausted
    logger.error(
        "tool_call_exhausted func=%s retries=%s last_error=%s",
        func.__name__, retries, last_error,
    )

    return {
        "status": "failed",
        "reason": str(last_error),
    }
