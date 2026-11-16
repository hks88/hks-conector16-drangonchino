import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from fiscal_sequence import FiscalStore, MAX_NUMBER, fiscal_number, scope, scope_from_config

SCOPE = dict(emisor="TEST-RUC", ambiente="2", sucursal="0000", punto="001",
             odoo_database="odoo_pruebas", odoo_company_id="18", odoo_pos_config_id="24")


def payload(index=1, refund=False):
    return {"FISCAL_PROTOCOL": 2, "FISCAL_IDENTITY": dict(SCOPE), "ORDER_UID": f"{index:032x}", "IS_REFUND": refund,
            "RAZON_SOCIAL": False, "TIPO_CLIENTE": "02", "PARTNER_EMAIL": False,
            "ORDERLINES": [{"ITEM_ID": 1, "PRODUCT_NAME": "PRUEBA NO FISCAL", "PRICE": 1,
                            "DISCOUNT": 0, "QUANTITY": 1, "TAX": 0, "TAX_AMOUNT": 0,
                            "TOTAL_WITH_DISCOUNT": 1, "TOTAL_WITH_TAX": 1, "CPBS_CODE": False}],
            "PAYMENTLINES": [{"PAY_NAME": "Efectivo", "AMOUNT": 1}], "CHANGE": 0, "TOTAL_QUANTITY": 1}


def render(data, valid, refund):
    identity = data['FISCAL_IDENTITY']
    return f"L01|03|{'06' if refund else '01'}|{data['ORDER_NUMBER']}|{identity['punto']}|{identity['sucursal']}|2026-08-27T12:00:00-05:00|"


def render_complete(data, valid, refund):
    return (
        render(data, valid, refund)
        + f"\nL06|{data['ORDERLINES'][0]['PRODUCT_NAME']}|"
        + "\n"
        + "\n".join(
        f"L08|{line['PAY_NAME']}|{line['AMOUNT']}||"
        for line in data["PAYMENTLINES"]
        )
    )


class FiscalSequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fiscal_state.sqlite3"
        self.store = FiscalStore.initialize(self.path, SCOPE, 9981010242, 9517020262)

    def test_continues_ten_digits_above_32bit(self):
        record = self.store.prepare(payload(), render)
        self.assertEqual(record["number"], "9981010243")
        self.assertEqual(record["state"], "prepared")

    def test_restarts_reuse_same_number_and_exact_document(self):
        original = self.store.prepare(payload(), render)
        reopened = FiscalStore(self.path, SCOPE)
        retry = reopened.prepare({**payload(), "FISCAL_NUMBER": original["number"]}, lambda *args: self.fail("No regenerar TXT"))
        self.assertEqual(retry["document"], original["document"])
        self.assertEqual(reopened.status()["last_number"], 9981010243)

    def test_same_sale_concurrently_is_one_reservation(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            numbers = list(executor.map(lambda _: FiscalStore(self.path, SCOPE).prepare(payload(), render)["number"], range(24)))
        self.assertEqual(set(numbers), {"9981010243"})

    def test_distinct_sales_concurrently_have_unique_numbers(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            numbers = list(executor.map(lambda index: FiscalStore(self.path, SCOPE).prepare(payload(index), render)["number"], range(1, 25)))
        self.assertEqual(len(set(numbers)), 24)
        self.assertEqual(self.store.status()["last_number"], 9981010266)

    def test_invoice_and_credit_share_counter_and_keep_document_type(self):
        first = self.store.prepare(payload(), render)
        second = self.store.prepare(payload(2, True), render)
        self.assertEqual((first["number"], second["number"]), ("9981010243", "9981010244"))
        self.assertEqual(second["doc_type"], "06")

    def test_modified_sale_is_blocked_without_increment(self):
        self.store.prepare(payload(), render_complete)
        changed = copy.deepcopy(payload())
        changed["ORDERLINES"][0]["PRODUCT_NAME"] = "OTRO PRODUCTO"
        with self.assertRaisesRegex(ValueError, "Solo puede corregir"):
            self.store.prepare(changed, render_complete)
        self.assertEqual(self.store.status()["last_number"], 9981010243)

    def test_payment_can_be_corrected_while_prepared_using_same_number(self):
        original = self.store.prepare(payload(), render_complete)
        changed = copy.deepcopy(payload())
        changed["PAYMENTLINES"] = [{"PAY_NAME": "Tarjeta", "AMOUNT": 1}]
        amended = self.store.prepare(changed, render_complete)
        self.assertEqual(amended["number"], original["number"])
        self.assertEqual(amended["state"], "prepared")
        self.assertTrue(amended["amended_before_dispatch"])
        self.assertNotEqual(amended["payload_hash"], original["payload_hash"])
        self.assertIn(b"L08|Tarjeta|1||", amended["document"])
        self.assertEqual(self.store.status()["last_number"], 9981010243)

    def test_payment_correction_keeps_original_timestamp_after_time_changes(self):
        timestamps = iter(("2026-08-29T13:00:00-05:00", "2026-08-29T13:05:00-05:00"))

        def changing_time_renderer(data, valid, refund):
            identity = data["FISCAL_IDENTITY"]
            return (
                f"L01|03|01|{data['ORDER_NUMBER']}|{identity['punto']}|"
                f"{identity['sucursal']}|{next(timestamps)}|RESTO|\n"
                "L06|PRUEBA NO FISCAL|\n"
                + "\n".join(
                    f"L08|{line['PAY_NAME']}|{line['AMOUNT']}||"
                    for line in data["PAYMENTLINES"]
                )
            )

        original = self.store.prepare(payload(), changing_time_renderer)
        changed = copy.deepcopy(payload())
        changed["PAYMENTLINES"] = [{"PAY_NAME": "Tarjeta", "AMOUNT": 1}]
        amended = self.store.prepare(changed, changing_time_renderer)
        text = bytes(amended["document"]).decode("cp1252")
        self.assertIn("|2026-08-29T13:00:00-05:00|RESTO|", text.splitlines()[0])
        self.assertNotIn("13:05:00", text)
        self.assertEqual(amended["number"], original["number"])

    def test_payment_change_after_dispatch_is_blocked(self):
        self.store.prepare(payload(), render_complete)
        self.store.claim_dispatch(payload()["ORDER_UID"])
        changed = copy.deepcopy(payload())
        changed["PAYMENTLINES"] = [{"PAY_NAME": "Tarjeta", "AMOUNT": 1}]
        with self.assertRaisesRegex(ValueError, "ya fue enviada o respondida"):
            self.store.prepare(changed, render_complete)

    def test_order_status_is_read_only_and_only_missing_or_prepared_is_mutable(self):
        missing = self.store.order_status(payload()["ORDER_UID"])
        self.assertEqual(missing["state"], "missing")
        self.assertTrue(missing["request_mutable"])
        self.assertIsNone(missing["fiscal_number"])
        self.assertEqual(self.store.status()["last_number"], 9981010242)

        prepared = self.store.prepare(payload(), render_complete)
        status = self.store.order_status(prepared["order_uid"])
        self.assertEqual(status["state"], "prepared")
        self.assertEqual(status["fiscal_number"], prepared["number"])
        self.assertTrue(status["request_mutable"])

        self.store.claim_dispatch(prepared["order_uid"])
        dispatched = self.store.order_status(prepared["order_uid"])
        self.assertEqual(dispatched["state"], "dispatched")
        self.assertFalse(dispatched["request_mutable"])

    def test_number_in_odoo_without_record_never_allocates(self):
        with self.assertRaisesRegex(ValueError, "sin registro local"):
            self.store.prepare({**payload(), "FISCAL_NUMBER": "9981010243"}, render)
        self.assertEqual(self.store.status()["last_number"], 9981010242)

    def test_wrong_number_in_odoo_is_blocked(self):
        self.store.prepare(payload(), render)
        with self.assertRaisesRegex(ValueError, "difiere"):
            self.store.prepare({**payload(), "FISCAL_NUMBER": "9981010244"}, render)

    def test_renderer_failure_rolls_back_reservation(self):
        def fail(*args):
            raise ValueError("importe incorrecto")
        with self.assertRaisesRegex(ValueError, "importe"):
            self.store.prepare(payload(), fail)
        self.assertEqual(self.store.status()["last_number"], 9981010242)

    def test_exhaustion_does_not_wrap(self):
        with self.store.connect() as conn, conn:
            conn.execute("UPDATE counter SET last_number=?", (MAX_NUMBER - 1,))
        self.assertEqual(self.store.prepare(payload(), render)["number"], "9999999999")
        with self.assertRaisesRegex(ValueError, "agotado"):
            self.store.prepare(payload(2), render)

    def test_cannot_reinitialize_existing_state(self):
        with self.assertRaises(FileExistsError):
            FiscalStore.initialize(self.path, SCOPE, 0, 0)
        self.assertEqual(self.store.status()["last_number"], 9981010242)

    def test_missing_database_fails_without_creation(self):
        path = Path(self.temp.name) / "missing.sqlite3"
        with self.assertRaisesRegex(ValueError, "No se reiniciará"):
            FiscalStore(path, SCOPE).prepare(payload(), render)
        self.assertFalse(path.exists())

    def test_wrong_scope_fails(self):
        for field, value in (("emisor", "OTHER"), ("ambiente", "1"), ("sucursal", "0001"), ("punto", "002"),
                             ("odoo_database", "otra_base"), ("odoo_company_id", "19"), ("odoo_pos_config_id", "25")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "no corresponde"):
                FiscalStore(self.path, {**SCOPE, field: value}).status()

    def test_each_identity_dimension_is_checked_before_reservation(self):
        for field in SCOPE:
            with self.subTest(field=field):
                request = payload()
                request["FISCAL_IDENTITY"][field] = "OTRO"
                with self.assertRaisesRegex(ValueError, "otra compañía/emisor"):
                    self.store.prepare(request, lambda *_: self.fail("No renderizar otra compañía"))
        self.assertEqual(self.store.status()["last_number"], 9981010242)
        self.assertEqual(self.store.status()["states"], {})

    def test_missing_partial_and_old_protocol_fail_closed(self):
        for identity in (None, {}, {"emisor": SCOPE["emisor"]}, {**SCOPE, "extra": "1"}):
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, "identidad fiscal completa"):
                self.store.prepare({**payload(), "FISCAL_IDENTITY": identity}, render)
        for version in (1, True, "2", 2.0, None):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "incompatible"):
                self.store.prepare({**payload(), "FISCAL_PROTOCOL": version}, render)
        self.assertEqual(self.store.status()["last_number"], 9981010242)

    def test_same_point_and_same_uid_can_exist_for_two_separate_emitter_pcs(self):
        other_scope = {**SCOPE, "emisor": "OTHER-RUC", "odoo_company_id": "19", "odoo_pos_config_id": "25"}
        other = FiscalStore.initialize(Path(self.temp.name) / "pc_b.sqlite3", other_scope, 100, 0)
        request_b = {**payload(), "FISCAL_IDENTITY": other_scope}
        self.assertEqual(self.store.prepare(payload(), render)["number"], "9981010243")
        self.assertEqual(other.prepare(request_b, render)["number"], "0000000101")
        for store, request in ((other, payload()), (self.store, request_b)):
            with self.assertRaisesRegex(ValueError, "otra compañía/emisor"):
                store.prepare(request, render)
        self.assertEqual(other.status()["last_number"], 101)
        self.assertEqual(self.store.status()["last_number"], 9981010243)

    def test_customer_ruc_does_not_select_issuer(self):
        self.assertEqual(self.store.prepare({**payload(), "RUC": "RUC-DEL-CLIENTE"}, render)["number"], "9981010243")
        request = payload(2)
        del request["FISCAL_IDENTITY"]
        request["RUC"] = SCOPE["emisor"]
        with self.assertRaisesRegex(ValueError, "identidad fiscal completa"):
            self.store.prepare(request, render)

    def test_rendered_header_must_match_validated_scope(self):
        with self.assertRaisesRegex(ValueError, "cabecera TXT"):
            self.store.prepare(payload(), lambda *args: render(*args).replace("|001|0000|", "|002|0000|"))
        self.assertEqual(self.store.status()["last_number"], 9981010242)

    def test_old_state_is_not_silently_adopted_or_reset(self):
        with self.store.connect() as conn, conn:
            conn.execute("UPDATE metadata SET value='1' WHERE key='schema_version'")
        with self.assertRaisesRegex(ValueError, "No borre ni reinicialice"):
            self.store.prepare(payload(), render)

    def test_config_requires_explicit_odoo_binding(self):
        import configparser
        config = configparser.ConfigParser(interpolation=None)
        config.read_dict({'FiscalSequence': {'modo': 'local', **{key: value for key, value in SCOPE.items() if key not in ('sucursal', 'punto')}},
                          'Target': {'sucursal': SCOPE['sucursal'], 'punto_de_facturacion': SCOPE['punto']}})
        self.assertEqual(scope_from_config(config), SCOPE)
        config.remove_option('FiscalSequence', 'odoo_company_id')
        with self.assertRaisesRegex(ValueError, 'odoo_company_id'):
            scope_from_config(config)
        config['FiscalSequence']['modo'] = 'odoo'
        with self.assertRaisesRegex(ValueError, 'modo=local'):
            scope_from_config(config)

    def test_alphanumeric_branch_is_preserved_in_scope_state_and_txt(self):
        alphanumeric = {**SCOPE, "sucursal": "4B00"}
        path = Path(self.temp.name) / "alphanumeric.sqlite3"
        store = FiscalStore.initialize(path, alphanumeric, 41, 0)
        request = {**payload(), "FISCAL_IDENTITY": alphanumeric}
        record = store.prepare(request, render)
        self.assertEqual(store.status()["sucursal"], "4B00")
        self.assertIn(b"|001|4B00|", record["document"])

    def test_branch_accepts_only_four_uppercase_ascii_alphanumerics(self):
        self.assertEqual(scope(**{**SCOPE, "sucursal": "4B00"})["sucursal"], "4B00")
        for value in ("4b00", "4-00", "4 B0", "4B0", "4B000", "４B00", "ÁB00"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "sucursal"):
                scope(**{**SCOPE, "sucursal": value})

    def test_dispatch_claim_survives_restart(self):
        self.store.prepare(payload(), render)
        self.assertTrue(self.store.claim_dispatch(payload()["ORDER_UID"]))
        self.assertFalse(FiscalStore(self.path, SCOPE).claim_dispatch(payload()["ORDER_UID"]))

    def test_backup_preserves_counter_assignments_and_responses(self):
        record = self.store.prepare(payload(), render)
        self.store.save_response(payload()["ORDER_UID"], {"procesado": True})
        backup = Path(self.temp.name) / "backup.sqlite3"
        self.store.backup(backup)
        restored = FiscalStore(backup, SCOPE)
        self.assertEqual(restored.prepare(payload(), render)["number"], record["number"])
        self.assertIn('"procesado": true', restored.prepare(payload(), render)["response"])
        with self.assertRaises(FileExistsError):
            self.store.backup(backup)

    def test_fiscal_number_strictly_rejects_overflow_zero_and_path(self):
        for value in ("10001040043", "0000000000", "1", "../1234567", "１２３４５６７８９０", 1234567890, False):
            with self.subTest(value=value), self.assertRaises(ValueError):
                fiscal_number(value)


if __name__ == "__main__":
    unittest.main()
