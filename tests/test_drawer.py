from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import MagicMock, patch

import frontend


class DrawerPolicyTests(unittest.TestCase):
    def test_cash_mode_finds_cash_in_second_payment(self):
        payments = [
            {"PAY_NAME": "TarjetaCredito", "AMOUNT": 5.00},
            {"PAY_NAME": " Efectivo ", "AMOUNT": 5.00},
        ]
        self.assertTrue(frontend.should_open_drawer("efectivo", payments))

    def test_cash_mode_does_not_open_for_card_only(self):
        payments = [{"PAY_NAME": "TarjetaCredito", "AMOUNT": 10.00}]
        self.assertFalse(frontend.should_open_drawer("efectivo", payments))

    def test_always_mode_opens_without_payment_lines(self):
        self.assertTrue(frontend.should_open_drawer("siempre", []))

    def test_invalid_mode_falls_back_to_cash(self):
        payments = [{"PAY_NAME": "Yappy", "AMOUNT": 10.00}]
        self.assertFalse(frontend.should_open_drawer("desconocido", payments))


class DrawerHardwareTests(unittest.TestCase):
    def make_server(self):
        server = frontend.Server.__new__(frontend.Server)
        server.drawer_lock = threading.Lock()
        return server

    def test_printer_open_uses_escpos_command(self):
        server = self.make_server()
        printer_handle = object()

        with (
            patch.object(frontend, "printer_name", "POS Printer"),
            patch.object(frontend, "gaveta_port", ""),
            patch.object(
                frontend.win32print,
                "OpenPrinter",
                return_value=printer_handle,
            ),
            patch.object(frontend.win32print, "StartDocPrinter"),
            patch.object(frontend.win32print, "StartPagePrinter"),
            patch.object(frontend.win32print, "WritePrinter") as write_printer,
            patch.object(frontend.win32print, "EndPagePrinter"),
            patch.object(frontend.win32print, "EndDocPrinter"),
            patch.object(frontend.win32print, "ClosePrinter"),
        ):
            self.assertTrue(server.open_drawer("prueba"))
            write_printer.assert_called_once_with(
                printer_handle,
                b"\x1B\x70\x00\x19\xFA",
            )

    def test_serial_is_fallback_when_printer_fails(self):
        server = self.make_server()
        serial_connection = MagicMock()
        serial_context = MagicMock()
        serial_context.__enter__.return_value = serial_connection

        with (
            patch.object(frontend, "printer_name", "Missing Printer"),
            patch.object(frontend, "gaveta_port", "COM3"),
            patch.object(
                frontend.win32print,
                "OpenPrinter",
                side_effect=OSError("printer unavailable"),
            ),
            patch.object(
                frontend.serial,
                "Serial",
                return_value=serial_context,
            ) as serial_open,
        ):
            self.assertTrue(server.open_drawer("prueba"))
            serial_open.assert_called_once_with(
                "COM3",
                baudrate=9600,
                timeout=1,
            )
            serial_connection.write.assert_called_once_with(b"a")


class FiscalAckDrawerTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_ack_does_not_wait_for_slow_drawer(self):
        server = frontend.Server.__new__(frontend.Server)
        server.runtime = MagicMock()
        response = {"procesado": True, "replayed": False}
        server.runtime.service.process.return_value = response
        drawer_started = threading.Event()
        release_drawer = threading.Event()

        def slow_drawer(_source):
            drawer_started.set()
            release_drawer.wait(2)
            return True

        server.open_drawer = slow_drawer
        payload = {"ORDER_UID": "a" * 32, "PAYMENTLINES": [{"PAY_NAME": "Efectivo"}]}
        try:
            result = await asyncio.wait_for(
                server.process_local_order_event("sid", payload),
                timeout=0.5,
            )
            self.assertIs(result, response)
            self.assertTrue(drawer_started.wait(0.5))
        finally:
            release_drawer.set()

    async def test_replayed_response_does_not_open_drawer_again(self):
        server = frontend.Server.__new__(frontend.Server)
        server.runtime = MagicMock()
        server.runtime.service.process.return_value = {"procesado": True, "replayed": True}
        server.open_drawer_in_background = MagicMock()

        result = await server.process_local_order_event(
            "sid",
            {"ORDER_UID": "a" * 32, "PAYMENTLINES": [{"PAY_NAME": "Efectivo"}]},
        )

        self.assertTrue(result["procesado"])
        server.open_drawer_in_background.assert_not_called()


if __name__ == "__main__":
    unittest.main()
