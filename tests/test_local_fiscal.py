from pathlib import Path
import copy
import tempfile
import unittest
from unittest.mock import patch

import local_fiscal
from local_fiscal import LocalFiscalService, publish_once
from fiscal_sequence import FiscalStore
import order_process
from test_fiscal_sequence import SCOPE, payload


class LocalFiscalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agent = self.root / "agent"
        self.archive = self.agent / "Resultado_FF"
        self.agent.mkdir()
        self.archive.mkdir()
        self.store = FiscalStore.initialize(self.root / "state.sqlite3", SCOPE, 9981010242, 9517020262)
        for name, value in (("n_sucursal", "0000"), ("punto_facturacion", "001")):
            patcher = patch.object(order_process, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = LocalFiscalService(self.store, self.agent, self.archive, timeout=0)

    def response(self, number="9981010243", archived=False, content=None):
        path = (self.archive if archived else self.agent) / f"result_{number}_1.txt"
        path.write_text(content or "CUFE-PRUEBA\nhttps://example.test/cafe\n2026-08-27T12:00:00-05:00\n", encoding="utf-8")
        return path

    def test_render_uses_new_fiscal_number_and_scope(self):
        result = self.service.process(payload())
        path = self.agent / "9981010243.txt"
        self.assertIn("L01|03|01|9981010243|001|0000|", path.read_text(encoding="cp1252"))
        self.assertTrue(result["pending"])
        self.assertFalse(result["procesado"])
        self.assertEqual(result["fiscal_identity"], SCOPE)
        self.assertEqual(result["protocol"], 2)

    def test_cross_company_is_rejected_without_txt_or_counter_change(self):
        request = payload()
        request["FISCAL_IDENTITY"]["odoo_company_id"] = "999"
        with patch.object(local_fiscal, "publish_once") as publish:
            with self.assertRaisesRegex(ValueError, "otra compañía/emisor"):
                self.service.process(request)
        publish.assert_not_called()
        self.assertEqual(list(self.agent.glob("*.txt")), [])
        self.assertEqual(self.store.status()["last_number"], 9981010242)

    def test_status_query_is_scoped_and_has_no_counter_or_queue_side_effects(self):
        request = {
            "FISCAL_PROTOCOL": 2,
            "FISCAL_IDENTITY": dict(SCOPE),
            "ORDER_UID": payload()["ORDER_UID"],
        }
        result = self.service.request_status(request)
        self.assertEqual(result["state"], "missing")
        self.assertTrue(result["request_mutable"])
        self.assertEqual(result["protocol"], 2)
        self.assertEqual(result["fiscal_identity"], SCOPE)
        self.assertEqual(self.store.status()["last_number"], 9981010242)
        self.assertEqual(list(self.agent.glob("*.txt")), [])

        wrong = copy.deepcopy(request)
        wrong["FISCAL_IDENTITY"]["odoo_company_id"] = "999"
        with self.assertRaisesRegex(ValueError, "otra compañía"):
            self.service.request_status(wrong)

    def test_local_renderer_uses_validated_profile_not_global_values(self):
        with patch.object(order_process, 'n_sucursal', '9999'), patch.object(order_process, 'punto_facturacion', '999'):
            self.service.process(payload())
        self.assertIn('|001|0000|', (self.agent / '9981010243.txt').read_text(encoding='cp1252'))

    def test_business_invoice_and_refund_preserve_existing_txt_modes_and_types(self):
        for index, refund, doc_type in ((1, False, '01'), (2, True, '09')):
            request = payload(index, refund)
            request.update(RAZON_SOCIAL='CLIENTE PRUEBA', TIPO_CLIENTE='01', tipo_receptor='1',
                           RUC='RUC-RECEPTOR', DV='1', DIRECCION='PRUEBA', COD_UBI='8-8-1',
                           CORREGIMIENTO='PRUEBA', DISTRITO='PRUEBA', PROVINCIA='PRUEBA')
            result = self.service.process(request)
            number = result['fiscal_number']
            self.assertIn(f'L01|01|{doc_type}|{number}|001|0000|', (self.agent / f'{number}.txt').read_text(encoding='cp1252'))
            self.assertEqual(self.store.prepare(request, self.service.renderer)['doc_type'], doc_type)

    def test_timeout_restart_and_retry_do_not_republish(self):
        self.service.process(payload())
        # Simula que el Agente consumió el TXT, sin respuesta aún.
        (self.agent / "9981010243.txt").unlink()
        service = LocalFiscalService(FiscalStore(self.store.path, SCOPE), self.agent, self.archive, timeout=0)
        with patch.object(local_fiscal, "publish_once") as publish:
            retry = service.process(payload())
        publish.assert_not_called()
        self.assertTrue(retry["pending"])
        self.assertEqual(self.store.status()["last_number"], 9981010243)

    def test_real_payment_correction_updates_prepared_document_and_keeps_number(self):
        original_request = payload()
        original = self.store.prepare(original_request, self.service.renderer)
        changed = copy.deepcopy(original_request)
        changed["PAYMENTLINES"] = [
            {"PAY_NAME": "Tarjeta", "PAYMENT_CODE": "99", "AMOUNT": 1}
        ]
        result = self.service.process(changed)
        self.assertTrue(result["pending"])
        self.assertEqual(result["fiscal_number"], original["number"])
        document = (self.agent / f"{original['number']}.txt").read_text(
            encoding="cp1252"
        )
        self.assertIn("\nL08|99|1.00|", document)
        self.assertEqual(self.store.status()["last_number"], 9981010243)

    def test_invalid_real_payment_does_not_reserve_and_corrected_retry_can_continue(self):
        invalid = payload()
        invalid["PAYMENTLINES"] = [
            {"PAY_NAME": "Efectivo", "AMOUNT": 1},
            {"PAY_NAME": "Tarjeta", "PAYMENT_CODE": "99", "AMOUNT": 10},
        ]
        with self.assertRaisesRegex(ValueError, "importe negativo"):
            self.service.process(invalid)
        self.assertEqual(self.store.status()["last_number"], 9981010242)
        self.assertEqual(self.store.status()["states"], {})
        corrected = self.service.process(payload())
        self.assertEqual(corrected["fiscal_number"], "9981010243")
        self.assertTrue(corrected["pending"])

    def test_late_archived_response_is_recovered_and_cached(self):
        self.service.process(payload())
        path = self.response(archived=True)
        result = self.service.process(payload())
        self.assertTrue(result["procesado"])
        path.unlink()
        retry = self.service.process(payload())
        self.assertTrue(retry["procesado"])
        self.assertTrue(retry["replayed"])
        self.assertEqual(retry["contenido"], result["contenido"])

    def test_offline_watcher_archives_late_response_but_status_does_not_reconcile_it(self):
        # Respuesta tardía simulada dentro del directorio temporal de la prueba.
        request = payload()
        first = self.service.process(request)
        number = first["fiscal_number"]
        self.assertTrue(first["pending"])
        result_file = self.response(number)
        with patch.object(local_fiscal.order_return, "AGENT_DIR", self.agent), patch.object(
            local_fiscal.order_return, "RESULT_DIR", self.archive
        ):
            archived = local_fiscal.order_return.archivar_resultados_pendientes(antiguedad_minima=0)
        self.assertEqual(archived, 1)
        self.assertFalse(result_file.exists())
        self.assertTrue((self.archive / result_file.name).exists())
        status_request = {key: request[key] for key in ("FISCAL_PROTOCOL", "FISCAL_IDENTITY", "ORDER_UID")}
        with patch.object(local_fiscal, "publish_once") as publish:
            status = self.service.request_status(status_request)
            self.assertEqual(status["state"], "dispatched")
            self.assertFalse(status["request_mutable"])
            self.assertEqual(self.store.status()["states"], {"dispatched": 1})
            recovered = self.service.process(request)
        publish.assert_not_called()
        self.assertTrue(recovered["procesado"])
        self.assertEqual(recovered["fiscal_number"], number)
        self.assertEqual(self.store.status()["last_number"], int(number))
        self.assertEqual(self.store.status()["states"], {"responded": 1})

    def test_offline_changed_content_is_blocked_even_with_archived_response(self):
        request = payload()
        first = self.service.process(request)
        self.response(first["fiscal_number"], archived=True)
        changed = copy.deepcopy(request)
        changed["ORDERLINES"][0].update(ITEM_ID=999, PRODUCT_NAME="OTRO PRODUCTO", PRICE=6.50)
        with patch.object(local_fiscal, "publish_once") as publish, patch.object(
            local_fiscal, "find_result", wraps=local_fiscal.find_result
        ) as find_result:
            with self.assertRaisesRegex(ValueError, "su contenido cambió"):
                self.service.process(changed)
        publish.assert_not_called()
        find_result.assert_not_called()
        self.assertEqual(self.store.status()["last_number"], int(first["fiscal_number"]))
        self.assertEqual(self.store.status()["states"], {"dispatched": 1})

    def test_rejection_is_saved_without_new_number_or_automatic_resend(self):
        self.service.process(payload())
        path = self.response(content="Error\nDocumento rechazado de prueba\n")
        self.assertFalse(self.service.process(payload())["procesado"])
        path.unlink()
        with patch.object(local_fiscal, "publish_once") as publish:
            retry = self.service.process(payload())
        publish.assert_not_called()
        self.assertIn("rechazado", retry["mensaje"])
        self.assertTrue(retry["replayed"])

    def test_crash_after_claim_never_republishes_ambiguous_document(self):
        record = self.store.prepare(payload(), order_process.render_document)
        self.store.claim_dispatch(record["order_uid"])
        result = self.service.process(payload())
        self.assertTrue(result["pending"])
        self.assertFalse((self.agent / "9981010243.txt").exists())

    def test_publication_never_overwrites_existing_file(self):
        target = self.agent / "9981010243.txt"
        target.write_bytes(b"existing")
        result = self.service.process(payload())
        self.assertTrue(result["pending"])
        self.assertEqual(target.read_bytes(), b"existing")

    def test_atomic_publication_has_complete_content_and_removes_temp(self):
        target = self.agent / "safe.txt"
        publish_once(target, b"L01|complete\nL07|complete")
        self.assertEqual(target.read_bytes(), b"L01|complete\nL07|complete")
        self.assertEqual(list(self.agent.glob("*.pending")), [])
        with self.assertRaises(FileExistsError):
            publish_once(target, b"replacement")

    def test_result_poll_interval_is_bounded_to_fifty_milliseconds(self):
        with patch.object(
            local_fiscal.time,
            "monotonic",
            side_effect=[0.0, 0.0, 0.2],
        ), patch.object(local_fiscal.time, "sleep") as sleep, patch.object(
            local_fiscal,
            "matching_files",
            return_value=[],
        ) as matching:
            result = local_fiscal.find_result(
                "9981010243",
                self.agent,
                self.archive,
                timeout=0.2,
            )
        self.assertIsNone(result)
        sleep.assert_called_once_with(0.05)
        searched_folders = [call.args[0] for call in matching.call_args_list]
        self.assertEqual(searched_folders.count(self.archive), 1)

    def test_fresh_order_does_not_scan_result_archive(self):
        with patch.object(
            local_fiscal,
            "matching_files",
            wraps=local_fiscal.matching_files,
        ) as matching:
            self.service.process(payload())
        searched_folders = [call.args[0] for call in matching.call_args_list]
        self.assertNotIn(self.archive, searched_folders)

    def test_native_result_filter_is_sent_to_windows(self):
        if local_fiscal.win32file is None:
            self.skipTest("FindFilesW solo está disponible en Windows")
        with patch.object(
            local_fiscal.win32file,
            "FindFilesW",
            return_value=[],
        ) as find_files:
            self.assertEqual(
                local_fiscal.matching_files(
                    self.archive,
                    "result_9981010243_*",
                ),
                [],
            )
        find_files.assert_called_once_with(
            str(self.archive / "result_9981010243_*")
        )

    def test_missing_win32_transitive_dependency_falls_back_without_failing_sale(self):
        if local_fiscal.win32file is None:
            self.skipTest("FindFilesW solo está disponible en Windows")
        expected = self.response(archived=True)
        with patch.object(
            local_fiscal.win32file,
            "FindFilesW",
            side_effect=ModuleNotFoundError("No module named 'win32timezone'"),
        ):
            matches = local_fiscal.matching_files(
                self.archive,
                "result_9981010243_*",
            )
        self.assertEqual(matches, [expected])

    def test_invalid_or_partial_response_stays_pending(self):
        self.service.process(payload())
        self.response(content="CUFE-PARCIAL\n")
        result = self.service.process(payload())
        self.assertTrue(result["pending"])
        self.assertEqual(self.store.status()["states"], {"dispatched": 1})

    def test_standalone_abono_keeps_existing_policy_without_consuming_number(self):
        request = payload()
        request["ORDERLINES"][0]["PRODUCT_NAME"] = "Abono"
        result = self.service.process(request)
        self.assertTrue(result["sin_documento"])
        self.assertTrue(result["procesado"])
        self.assertIsNone(result["fiscal_number"])
        self.assertEqual(self.store.status()["last_number"], 9981010242)
        self.assertEqual(list(self.agent.glob("*.txt")), [])
        changed = payload()
        with self.assertRaisesRegex(ValueError, "contenido cambió"):
            self.service.process(changed)


if __name__ == "__main__":
    unittest.main()
