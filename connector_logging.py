from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "factura_facil_connector"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

_configure_lock = threading.Lock()
_active_log_path: Path | None = None
_hooks_installed = False


def application_dir() -> Path:
    """Devuelve la carpeta externa del script o ejecutable PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_log_path() -> Path:
    return application_dir() / "log.txt"


def _fallback_log_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "FacturaFacilConnector" / "log.txt"
    return Path.cwd() / "log.txt"


def _open_file_handler(path: Path) -> tuple[RotatingFileHandler, Path]:
    candidates = [path]
    fallback = _fallback_log_path()
    if fallback != path:
        candidates.append(fallback)

    last_error: OSError | None = None
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                candidate,
                maxBytes=MAX_LOG_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            return handler, candidate
        except OSError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise OSError("No fue posible crear el archivo de log")


def configure_logging(
    log_path: str | Path | None = None,
    *,
    force: bool = False,
    console: bool = True,
) -> logging.Logger:
    """Configura un log rotativo compartido por todos los módulos."""
    global _active_log_path

    logger = logging.getLogger(LOGGER_NAME)
    with _configure_lock:
        if logger.handlers and not force:
            return logger

        if force:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()

        requested_path = Path(log_path) if log_path else default_log_path()
        file_handler, actual_path = _open_file_handler(requested_path)
        formatter = logging.Formatter(
            fmt=(
                "%(asctime)s.%(msecs)03d | %(levelname)s | %(threadName)s | "
                "%(name)s | %(message)s"
            ),
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)

        if console and sys.stdout is not None:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            console_handler.setLevel(logging.INFO)
            logger.addHandler(console_handler)

        logger.setLevel(logging.INFO)
        logger.propagate = False
        _active_log_path = actual_path
        return logger


def get_logger(component: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"{LOGGER_NAME}.{component}")


def active_log_path() -> Path:
    configure_logging()
    return _active_log_path or default_log_path()


def install_exception_hooks() -> None:
    """Registra excepciones no controladas del proceso y de sus hilos."""
    global _hooks_installed
    if _hooks_installed:
        return

    logger = get_logger("uncaught")
    previous_sys_hook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def sys_hook(exc_type, exc_value, exc_traceback) -> None:
        logger.critical(
            "Excepción no controlada en el proceso principal",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        if previous_sys_hook is not sys.__excepthook__:
            previous_sys_hook(exc_type, exc_value, exc_traceback)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        thread_name = args.thread.name if args.thread is not None else "desconocido"
        logger.critical(
            "Excepción no controlada en el hilo %s",
            thread_name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        if previous_thread_hook is not threading.__excepthook__:
            previous_thread_hook(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook
    _hooks_installed = True


def log_asyncio_exception(_loop, context: dict) -> None:
    """Registra errores asíncronos que no llegaron a un await controlado."""
    logger = get_logger("asyncio")
    exception = context.get("exception")
    message = context.get("message", "Error asíncrono no controlado")
    if exception is not None:
        logger.error(
            "%s",
            message,
            exc_info=(type(exception), exception, exception.__traceback__),
        )
    else:
        logger.error("%s | contexto=%r", message, context)
