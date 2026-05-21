import logging
import sys
from contextvars import ContextVar

import structlog

_run_id_ctx: ContextVar[str | None] = ContextVar("run_id", default=None)


def bind_run_id(run_id: str | None) -> None:
    _run_id_ctx.set(run_id)


def _inject_run_id(_: object, __: str, event_dict: dict) -> dict:
    rid = _run_id_ctx.get()
    if rid is not None:
        event_dict.setdefault("run_id", rid)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level.upper(),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_run_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
