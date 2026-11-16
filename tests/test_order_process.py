from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import order_process


class PaymentTests(unittest.TestCase):
    def test_yappy_name_uses_supported_fel_code_08(self):
        total_received, linea8 = order_process.payment(
            [{"PAY_NAME": "Yappy", "AMOUNT": 10.00}],
            invoice_total=10.00,
            change=0,
        )

        self.assertEqual(total_received, Decimal("10.00"))
        self.assertIn("L08|08|10.00||", linea8)

    def test_supported_explicit_payment_code_takes_precedence_over_name(self):
        cases = (
            ("04", "Efectivo"),
            ("03", "Efectivo"),
            ("02", "Yappy"),
            (9, "Cheque"),
        )
        for code, name in cases:
            with self.subTest(code=code, name=name):
                total_received, linea8 = order_process.payment(
                    [{"PAY_NAME": name, "PAYMENT_CODE": code, "AMOUNT": 10}],
                    invoice_total=10,
                    change=0,
                )
                expected_code = str(code).zfill(2)
                self.assertEqual(total_received, Decimal("10.00"))
                self.assertIn(f"L08|{expected_code}|10.00||", linea8)

    def test_internal_yappy_code_11_is_translated_to_fel_08(self):
        _, linea8 = order_process.payment(
            [{"PAY_NAME": "Yappy", "PAYMENT_CODE": "11", "AMOUNT": 10}],
            invoice_total=10,
            change=0,
        )
        self.assertIn("L08|08|10.00||", linea8)
        self.assertNotIn("L08|11|", linea8)

    def test_internal_coalapay_code_12_becomes_other_with_description(self):
        _, linea8 = order_process.payment(
            [{"PAY_NAME": "CoalaPay", "PAYMENT_CODE": "12", "AMOUNT": 10}],
            invoice_total=10,
            change=0,
        )
        self.assertIn("L08|99|10.00|Pago por CoalaPay|", linea8)
        self.assertNotIn("L08|12|", linea8)

    def test_unknown_numeric_code_never_reaches_agent(self):
        _, linea8 = order_process.payment(
            [{"PAY_NAME": "NuevaBilletera", "PAYMENT_CODE": "13", "AMOUNT": 10}],
            invoice_total=10,
            change=0,
        )
        self.assertIn("L08|99|10.00|NuevaBilletera|", linea8)
        self.assertNotIn("L08|13|", linea8)

    def test_other_payment_description_meets_minimum_length(self):
        _, linea8 = order_process.payment(
            [{"PAY_NAME": "X", "PAYMENT_CODE": "99", "AMOUNT": 10}],
            invoice_total=10,
            change=0,
        )
        self.assertIn("L08|99|10.00|Pago por X|", linea8)

    def test_payment_without_explicit_code_keeps_name_fallback(self):
        _, linea8 = order_process.payment(
            [{"PAY_NAME": "Transferencia", "AMOUNT": 10}],
            invoice_total=10,
            change=0,
        )
        self.assertIn("L08|08|10.00||", linea8)

    def test_invalid_explicit_payment_code_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "PAYMENT_CODE inválido"):
            order_process.payment(
                [{"PAY_NAME": "Yappy", "PAYMENT_CODE": "Yappy", "AMOUNT": 10}],
                invoice_total=10,
                change=0,
            )

    def test_exact_cash_payment(self):
        total_received, linea8 = order_process.payment(
            [{"PAY_NAME": "Efectivo", "AMOUNT": 3.01}],
            invoice_total=3.01,
            change=0,
        )
        self.assertEqual(total_received, Decimal("3.01"))
        self.assertIn("L08|02|3.01||", linea8)

    def test_cash_payment_is_adjusted_to_include_change(self):
        total_received, linea8 = order_process.payment(
            [{"PAY_NAME": "Efectivo", "AMOUNT": 3.01}],
            invoice_total=3.01,
            change=2.00,
        )
        self.assertEqual(total_received, Decimal("5.01"))
        self.assertIn("L08|02|5.01||", linea8)

    def test_inconsistent_mixed_payment_without_cash_is_rejected(self):
        with self.assertRaises(ValueError):
            order_process.payment(
                [
                    {"PAY_NAME": "TarjetaCredito", "AMOUNT": 1.00},
                    {"PAY_NAME": "Yappy", "AMOUNT": 1.00},
                ],
                invoice_total=5.00,
                change=0,
            )

    def test_text_separator_is_cleaned(self):
        self.assertEqual(
            order_process.clean_text_field('TV | 32" | HD\nHDMI'),
            'TV / 32" / HD HDMI',
        )


class TxtGenerationTests(unittest.TestCase):
    def test_discount_greater_than_unit_price_is_rejected(self):
        payload = {
            "RAZON_SOCIAL": False,
            "RUC": False,
            "DV": False,
            "TIPO_CLIENTE": False,
            "DIRECCION": None,
            "COUNTRY": None,
            "PROVINCIA": None,
            "DISTRITO": None,
            "CORREGIMIENTO": None,
            "COD_UBI": None,
            "PARTNER_EMAIL": False,
            "ORDER_NUMBER": "2998040002",
            "ORDERLINES": [
                {
                    "PRICE": "0.79",
                    "PRODUCT_NAME": "PRODUCTO CON DESCUENTO PORCENTUAL",
                    "QUANTITY": 1,
                    "TAX": 7,
                    "CPBS_CODE": False,
                    "DISCOUNT": "25",
                    "ITEM_ID": 1,
                    "SERIAL": False,
                }
            ],
            "PAYMENTLINES": [{"PAY_NAME": "Efectivo", "AMOUNT": "0.79"}],
        }

        with self.assertRaisesRegex(ValueError, "DISCOUNT .* es mayor que PRICE"):
            order_process.order(payload, valid=True, is_refund=False)

    def test_legacy_odoo_payload_derives_missing_totals_and_item_count(self):
        # El módulo Odoo 2.2.1 no envía los totales calculados por línea ni
        # TOTAL_QUANTITY en la orden. El conector debe reconstruirlos.
        payload = {
            "RAZON_SOCIAL": False,
            "RUC": False,
            "DV": False,
            "TIPO_CLIENTE": False,
            "DIRECCION": None,
            "COUNTRY": None,
            "PROVINCIA": None,
            "DISTRITO": None,
            "CORREGIMIENTO": None,
            "COD_UBI": None,
            "PARTNER_EMAIL": False,
            "ORDER_NUMBER": "2998040001",
            "ORDERLINES": [
                {
                    "PRICE": "1.60",
                    "PRODUCT_NAME": "PRODUCTO LEGACY",
                    "QUANTITY": 1,
                    "TAX": 7,
                    "CPBS_CODE": False,
                    "DISCOUNT": "0.10",
                    "ITEM_ID": 1,
                    "SERIAL": False,
                }
            ],
            "PAYMENTLINES": [{"PAY_NAME": "Efectivo", "AMOUNT": "1.61"}],
        }

        original_agent_dir = order_process.AGENT_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                order_process.AGENT_DIR = Path(temp_dir)
                output = order_process.order(payload, valid=True, is_refund=False)
                self.assertIsNotNone(output)
                lines = Path(output).read_text(encoding="cp1252").splitlines()

                item_line = next(line for line in lines if line.startswith("L06|"))
                self.assertEqual(item_line.split("|")[15], "1.61")

                total_line = next(line for line in lines if line.startswith("L07|"))
                total_fields = total_line.split("|")
                self.assertEqual(total_fields[5], "1.61")
                self.assertEqual(total_fields[13], "1")
        finally:
            order_process.AGENT_DIR = original_agent_dir

    def test_missing_unit_uses_und_fallback(self):
        payload = {
            "RAZON_SOCIAL": False,
            "RUC": False,
            "DV": False,
            "TIPO_CLIENTE": False,
            "tipo_receptor": False,
            "DIRECCION": None,
            "COUNTRY": None,
            "PROVINCIA": None,
            "DISTRITO": None,
            "CORREGIMIENTO": None,
            "COD_UBI": None,
            "PARTNER_EMAIL": False,
            "ORDER_NUMBER": "0000000001",
            "ORDERLINES": [
                {
                    "PRICE": 1.50,
                    "PRODUCT_NAME": "AGUA | 600ML",
                    "QUANTITY": 1,
                    "TAX": 0,
                    "TAX_AMOUNT": 0,
                    "CPBS_CODE": False,
                    "DISCOUNT": 0,
                    "TOTAL_WITH_DISCOUNT": 1.50,
                    "TOTAL_WITH_TAX": 1.50,
                    "ITEM_ID": 1,
                    "SERIAL": False,
                }
            ],
            "PAYMENTLINES": [{"PAY_NAME": "Efectivo", "AMOUNT": 1.50}],
            "CHANGE": 0,
            "TOTAL_QUANTITY": 1,
        }

        original_agent_dir = order_process.AGENT_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                order_process.AGENT_DIR = Path(temp_dir)
                output = order_process.order(payload, valid=True, is_refund=False)
                self.assertIsNotNone(output)
                content = Path(output).read_text(encoding="cp1252")
                self.assertIn("AGUA / 600ML", content)
                self.assertIn("|und|", content)
                self.assertNotIn("|False|", content)
        finally:
            order_process.AGENT_DIR = original_agent_dir

    def test_item_itbms_written_to_l06_matches_total_in_l07(self):
        # Regresión del rechazo DGI 2501 observado en la factura 0449010003:
        # Odoo envía C402 con 3-4 decimales, pero el TXT fiscal debe escribir y
        # sumar exactamente los mismos importes monetarios de dos decimales.
        item_values = [
            ("0.50", 6, "3.00", "0.21", "3.21"),
            ("2.99", 1, "2.99", "0.2093", "3.20"),
            ("1.50", 4, "6.00", "0.42", "6.42"),
            ("1.00", 1, "1.00", "0.07", "1.07"),
            ("6.50", 1, "6.50", "0.455", "6.96"),
            ("2.50", 1, "2.50", "0.175", "2.68"),
            ("1.95", 1, "1.95", "0.1365", "2.09"),
            ("1.00", 1, "1.00", "0.07", "1.07"),
            ("12.99", 1, "12.99", "0.9093", "13.90"),
            ("14.95", 1, "14.95", "1.0465", "16.00"),
            ("14.90", 1, "14.90", "1.043", "15.94"),
            ("30.99", 1, "30.99", "2.1693", "33.16"),
            ("10.99", 1, "10.99", "0.7693", "11.76"),
        ]
        orderlines = [
            {
                "PRICE": price,
                "PRODUCT_NAME": f"PRODUCTO {index}",
                "QUANTITY": quantity,
                "TAX": 7,
                "TAX_AMOUNT": tax_amount,
                "CPBS_CODE": "5215",
                "DISCOUNT": 0,
                "TOTAL_WITH_DISCOUNT": subtotal,
                "TOTAL_WITH_TAX": total,
                "ITEM_ID": index,
                "SERIAL": False,
            }
            for index, (price, quantity, subtotal, tax_amount, total)
            in enumerate(item_values, start=1)
        ]
        payload = {
            "RAZON_SOCIAL": False,
            "RUC": False,
            "DV": False,
            "TIPO_CLIENTE": False,
            "tipo_receptor": False,
            "DIRECCION": None,
            "COUNTRY": None,
            "PROVINCIA": None,
            "DISTRITO": None,
            "CORREGIMIENTO": None,
            "COD_UBI": None,
            "PARTNER_EMAIL": False,
            "ORDER_NUMBER": "0449010003",
            "ORDERLINES": orderlines,
            "PAYMENTLINES": [{"PAY_NAME": "Efectivo", "AMOUNT": "117.46"}],
            "CHANGE": 0,
            "TOTAL_QUANTITY": 13,
        }
        expected_item_taxes = [
            "0.21", "0.21", "0.42", "0.07", "0.46", "0.18", "0.14",
            "0.07", "0.91", "1.05", "1.04", "2.17", "0.77",
        ]

        original_agent_dir = order_process.AGENT_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                order_process.AGENT_DIR = Path(temp_dir)
                output = order_process.order(payload, valid=True, is_refund=False)
                self.assertIsNotNone(output)
                lines = Path(output).read_text(encoding="cp1252").splitlines()
                item_lines = [line for line in lines if line.startswith("L06|")]
                item_taxes = [line.split("|")[10] for line in item_lines]
                self.assertEqual(item_taxes, expected_item_taxes)

                total_line = next(line for line in lines if line.startswith("L07|"))
                total_fields = total_line.split("|")
                self.assertEqual(total_fields[1], "109.76")
                self.assertEqual(total_fields[2], "7.70")
                self.assertEqual(total_fields[4], "7.70")
                self.assertEqual(total_fields[5], "117.46")
        finally:
            order_process.AGENT_DIR = original_agent_dir


if __name__ == "__main__":
    unittest.main()
