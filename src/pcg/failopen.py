import asyncio
import logging
from collections.abc import Awaitable, Callable
from threading import Lock

logger = logging.getLogger("pcg.failopen")


class AnalysisErrorCounter:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = Lock()

    def increment(self, name: str) -> None:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


analysis_errors = AnalysisErrorCounter()


def _log_failure(component: str, error: Exception) -> None:
    try:
        logger.warning("component=%s error=%s", component, type(error).__name__)
    except Exception:
        return


def guard[T](
    component: str,
    fn: Callable[..., T],
    *args: object,
    default: T,
    **kwargs: object,
) -> T:
    try:
        return fn(*args, **kwargs)
    except Exception as error:
        analysis_errors.increment(component)
        _log_failure(component, error)
        return default


async def aguard[T](
    component: str,
    awaitable_fn: Callable[..., Awaitable[T]],
    *args: object,
    default: T,
    **kwargs: object,
) -> T:
    try:
        return await awaitable_fn(*args, **kwargs)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        analysis_errors.increment(component)
        _log_failure(component, error)
        return default


__all__ = ["AnalysisErrorCounter", "aguard", "analysis_errors", "guard"]
