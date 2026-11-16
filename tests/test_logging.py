from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import connector_logging
import frontend
import order_return


def flush_connector_log() -> None:
    logger = logging.getLogger(connector_logging.LOGGER_NAME)
    for handler in logger.handlers:
        handler.flush()


class LoggingTests(unittest.TestCase):
    def test_log_contains_processing_error_and_traceback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "log.txt"
            connector_logging.configure_logging(
                log_path,
                force=True,
                console=False,
            )
            try:
                logger = connector_logging.get_logger("test")
                try:
                    raise ValueError("documento incompatible")
                except ValueError:
                    logger.exception("Error procesando documento")

                order_return.escribir_log("[ERROR] Error de integración con Odoo")
                flush_connector_log()
                content = log_path.read_text(encoding="utf-8")

                self.assertIn("Error procesando documento", content)
                self.assertIn("ValueError: documento incompatible", content)
                self.assertIn("Error de integración con Odoo", content)
                self.assertIn("MainThread", content)
                self.assertRegex(
                    content,
                    re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \|", re.MULTILINE),
                )
            finally:
                connector_logging.configure_logging(force=True, console=False)

    def test_malformed_odoo_event_is_logged_and_returned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "log.txt"
            connector_logging.configure_logging(
                log_path,
                force=True,
                console=False,
            )
            try:
                server = frontend.Server.__new__(frontend.Server)
                server.sio = AsyncMock()

                response = asyncio.run(
                    server.process_new_order_event("client-test", ({"legacy": True},))
                )

                self.assertFalse(response["success"])
                self.assertIn("Evento new_order incompatible", response["error"])
                server.sio.emit.assert_awaited_once()

                flush_connector_log()
                content = log_path.read_text(encoding="utf-8")
                self.assertIn("Error procesando documento recibido desde Odoo", content)
                self.assertIn("se recibieron 1", content)
            finally:
                connector_logging.configure_logging(force=True, console=False)


class OdooEventCompatibilityTests(unittest.TestCase):
    def test_current_event_arguments_are_accepted(self):
        order_data = {"ORDER_NUMBER": "0000000001"}
        parsed = frontend.parse_new_order_event(
            (order_data, True, False, True, False)
        )
        self.assertEqual(parsed, (order_data, True, False, True, False))

    def test_wrapped_event_arguments_are_accepted(self):
        order_data = {"ORDER_NUMBER": "0000000001"}
        parsed = frontend.parse_new_order_event(
            ([order_data, True, False],)
        )
        self.assertEqual(parsed, (order_data, True, False, False, False))


if __name__ == "__main__":
    unittest.main()
