from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import order_return


class OrderReturnWatcherTests(unittest.TestCase):
    def setUp(self):
        self.original_agent_dir = order_return.AGENT_DIR
        self.original_result_dir = order_return.RESULT_DIR
        self.temp_dir = tempfile.TemporaryDirectory()
        self.agent_dir = Path(self.temp_dir.name) / "Agente_FF"
        self.result_dir = self.agent_dir / "Resultado_FF"
        order_return.AGENT_DIR = self.agent_dir
        order_return.RESULT_DIR = self.result_dir
        order_return.ensure_directories()

    def tearDown(self):
        order_return.AGENT_DIR = self.original_agent_dir
        order_return.RESULT_DIR = self.original_result_dir
        with order_return._ACTIVE_ORDERS_LOCK:
            order_return._ACTIVE_ORDERS.clear()
        self.temp_dir.cleanup()

    def create_result(self, order_number: str, *, age_seconds: float = 0) -> Path:
        result = self.agent_dir / f"result_{order_number}_1"
        result.write_text(
            "FE0001\nhttps://example.test/comprobante\n2026-08-10T10:00:00-05:00\n",
            encoding="utf-8",
        )
        if age_seconds:
            timestamp = time.time() - age_seconds
            os.utime(result, (timestamp, timestamp))
        return result

    def test_watcher_skips_response_for_active_order(self):
        order_number = "9666010231"
        source = self.create_result(order_number, age_seconds=120)
        order_return._registrar_orden_activa(order_number)
        try:
            archived = order_return.archivar_resultados_pendientes(
                antiguedad_minima=0,
            )
        finally:
            order_return._liberar_orden_activa(order_number)

        self.assertEqual(archived, 0)
        self.assertTrue(source.is_file())
        self.assertFalse((self.result_dir / source.name).exists())

    def test_watcher_skips_recent_orphan_response(self):
        source = self.create_result("0000000001")

        archived = order_return.archivar_resultados_pendientes(
            antiguedad_minima=60,
        )

        self.assertEqual(archived, 0)
        self.assertTrue(source.is_file())

    def test_watcher_archives_stale_orphan_response(self):
        source = self.create_result("0000000002", age_seconds=120)

        archived = order_return.archivar_resultados_pendientes(
            antiguedad_minima=60,
        )

        self.assertEqual(archived, 1)
        self.assertFalse(source.exists())
        self.assertTrue((self.result_dir / source.name).is_file())

    def test_observer_keeps_result_available_during_watcher_sweep(self):
        order_number = "9666010231"
        source = self.create_result(order_number, age_seconds=120)

        def run_watcher_during_processing(_seconds: float) -> None:
            archived = order_return.archivar_resultados_pendientes(
                antiguedad_minima=0,
            )
            self.assertEqual(archived, 0)
            self.assertTrue(source.is_file())

        with patch.object(order_return.time, "sleep", run_watcher_during_processing):
            result = order_return.observar({"ORDER_NUMBER": order_number})

        self.assertTrue(result["procesado"])
        self.assertFalse(source.exists())
        self.assertTrue((self.result_dir / source.name).is_file())


class OrderReturnEncodingTests(unittest.TestCase):
    def test_reads_cp1252_error_message_with_accents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = Path(temp_dir) / "result_0000000001_1"
            result.write_bytes(
                "Error\nNúmero de documento inválido\n".encode("cp1252")
            )

            parsed = order_return.leer_archivo(result)

        self.assertFalse(parsed["procesado"])
        self.assertEqual(parsed["mensaje"], "Número de documento inválido")

    def test_reads_utf8_success_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = Path(temp_dir) / "result_0000000002_1"
            result.write_text(
                "FE0001\nhttps://example.test/comprobante\n2026-08-18T12:00:00-05:00\n",
                encoding="utf-8",
            )

            parsed = order_return.leer_archivo(result)

        self.assertTrue(parsed["procesado"])
        self.assertEqual(parsed["contenido"]["identificador"], "FE0001")

    def test_retries_empty_response_until_content_is_available(self):
        response = "Error\nDocumento duplicado\n".encode("cp1252")
        with patch.object(Path, "read_bytes", side_effect=[b"", response]), patch.object(
            order_return.time,
            "sleep",
        ) as sleep_mock:
            text = order_return._leer_texto("result_test_1", intentos=2, espera=0.01)

        self.assertEqual(text, "Error\nDocumento duplicado\n")
        sleep_mock.assert_called_once_with(0.01)

    def test_retries_temporary_file_lock(self):
        response = "Error\nUnidad de medida inválida\n".encode("cp1252")
        with patch.object(
            Path,
            "read_bytes",
            side_effect=[PermissionError("archivo bloqueado"), response],
        ), patch.object(order_return.time, "sleep") as sleep_mock:
            text = order_return._leer_texto("result_test_1", intentos=2, espera=0.01)

        self.assertIn("Unidad de medida inválida", text)
        sleep_mock.assert_called_once_with(0.01)


if __name__ == "__main__":
    unittest.main()
