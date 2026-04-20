import json
import logging.config
import sys


class JsonFormatter(logging.Formatter):
    """Format logs as structured JSON."""

    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Include exception if present
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        # Forward known `extra=` fields passed by callers
        for key in ("request_id", "tool", "session_id", "latency_ms", "error"):
            if hasattr(record, key):
                log_record[key] = getattr(record, key)
        return json.dumps(log_record, ensure_ascii=False)


# Logging configuration dictionary
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": JsonFormatter}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": sys.stdout,
        }
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "uvicorn": {"propagate": True},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"level": "INFO"},
    },
}

# Apply logging configuration
logging.config.dictConfig(LOGGING_CONFIG)

# Global logger instance to use across the app
logger = logging.getLogger("app")
