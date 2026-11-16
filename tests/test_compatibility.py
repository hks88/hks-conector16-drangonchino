import asyncio
import configparser
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import frontend
from fiscal_runtime import FiscalRuntime, LOCAL_MARKER, configured_mode
from fiscal_sequence import FiscalStore
from test_fiscal_sequence import SCOPE, payload


def config(mode=None):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_dict({'Target': {'sucursal':'0000', 'punto_de_facturacion':'001', 'gaveta':'COM9', 'Nombre_impresora':'Conservar'}})
    if mode is not None:
        parser.read_dict({'FiscalSequence': {'modo':mode, **{key:value for key,value in SCOPE.items() if key not in ('sucursal','punto')}}})
    return parser


def legacy(number='9981010242'):
    return {'ORDER_NUMBER':number, **{key:value for key,value in payload().items()
                                   if key not in ('FISCAL_PROTOCOL','FISCAL_IDENTITY','ORDER_UID','IS_REFUND')}}


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ff_compat_SIMULADO_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agent = self.root / 'agent'
        self.agent.mkdir()
        self.results = self.agent / 'Resultado_FF'

    def runtime(self, mode=None):
        return FiscalRuntime(config(mode), self.root, self.agent, self.results, timeout=0)

    def server(self, runtime):
        server = frontend.Server.__new__(frontend.Server)
        server.runtime = runtime
        server.sio = AsyncMock()
        server.open_drawer = lambda *_: False
        return server

    def result(self, number='9981010242', archived=False):
        folder = self.results if archived else self.agent
        folder.mkdir(exist_ok=True)
        (folder / ('result_' + number + '_1.txt')).write_text('CUFE-SIMULADO\nhttps://example.test/cafe\n2026-08-28T11:00:00-05:00\n')

    def test_old_configuration_keeps_mode_and_hardware(self):
        old = config()
        before = {name:dict(old[name]) for name in old.sections()}
        runtime = FiscalRuntime(old,self.root,self.agent,self.results,timeout=0)
        self.assertEqual(runtime.mode,'odoo')
        self.assertEqual(before,{name:dict(old[name]) for name in old.sections()})
        self.assertFalse((self.root/'fiscal_state.sqlite3').exists())
        self.assertEqual(runtime.capabilities()['protocols'],[0])

    def test_alphanumeric_branch_starts_in_legacy_mode_without_rewriting_it(self):
        old = config()
        old['Target']['sucursal'] = '4B00'
        runtime = FiscalRuntime(old, self.root, self.agent, self.results, timeout=0)
        self.assertEqual(runtime.service.scope['sucursal'], '4B00')

    def test_invalid_branch_is_rejected_in_legacy_mode(self):
        for branch in ('4b00', '4-00', '4B0', '４B00'):
            old = config()
            old['Target']['sucursal'] = branch
            with self.subTest(branch=branch), self.assertRaisesRegex(ValueError, 'sucursal'):
                FiscalRuntime(old, self.root, self.agent, self.results, timeout=0)

    def test_partial_local_section_never_defaults_to_legacy(self):
        for mode in ('','LOCAL','invalid'):
            with self.assertRaises(ValueError):
                configured_mode(config(mode))
        parser = config('local'); parser.remove_option('FiscalSequence','modo')
        with self.assertRaises(ValueError):
            configured_mode(parser)

    def test_local_missing_state_blocks_startup_without_creating_counter(self):
        with self.assertRaisesRegex(ValueError,'Falta fiscal_state'):
            self.runtime('local')
        self.assertFalse((self.root/'fiscal_state.sqlite3').exists())
        with patch.object(frontend,'config',config('local')), patch.object(frontend,'BASE_DIR',self.root), patch.object(frontend.socketio,'AsyncServer') as start:
            with self.assertRaises(ValueError):
                frontend.Server()
            start.assert_not_called()

    def test_521_state_is_kept_and_marks_local_activation(self):
        path = self.root/'fiscal_state.sqlite3'
        FiscalStore.initialize(path,SCOPE,368,369)
        original = hashlib.sha256(path.read_bytes()).hexdigest()
        runtime = self.runtime('local')
        self.assertEqual(runtime.service.store.status()['last_number'],369)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),original)
        self.assertTrue((self.root/LOCAL_MARKER).is_file())
        self.assertEqual(runtime.capabilities()['identity'],SCOPE)
        self.runtime('local')  # Reiniciar no cambia nada.

    def test_actual_521_pending_store_is_reused_without_new_number_or_txt(self):
        source=Path(__file__).resolve().parent/'fixtures/connector-5.2.1/fiscal_sequence.py'
        spec=importlib.util.spec_from_file_location('original_521_sequence',source)
        previous=importlib.util.module_from_spec(spec); spec.loader.exec_module(previous)
        from test_fiscal_sequence import render
        old=previous.FiscalStore.initialize(self.root/'fiscal_state.sqlite3',SCOPE,368,369)
        record=old.prepare(payload(),render)
        old.claim_dispatch(record['order_uid'])
        runtime=self.runtime('local')
        result=runtime.service.process(payload())
        self.assertTrue(result['pending'])
        self.assertEqual(result['fiscal_number'],record['number'])
        self.assertEqual(runtime.service.store.status()['last_number'],370)
        self.assertEqual(list(self.agent.glob('*.txt')),[])

    def test_lost_sqlite_cannot_be_reinitialized_after_activation(self):
        FiscalStore.initialize(self.root/'fiscal_state.sqlite3',SCOPE,368,369)
        self.runtime('local')
        (self.root/'fiscal_state.sqlite3').unlink()
        with self.assertRaisesRegex(ValueError,'ya activó'):
            FiscalStore.initialize(self.root/'fiscal_state.sqlite3',SCOPE,0,0)
        self.assertFalse((self.root/'fiscal_state.sqlite3').exists())

    def test_legacy_abono_returns_old_success_without_number_or_file(self):
        request=legacy(); request['ORDERLINES'][0]['PRODUCT_NAME']='Abono'
        result=self.runtime().service.process(request,True,False)
        self.assertTrue(result['success'])
        self.assertTrue(result['sin_documento'])
        self.assertFalse(result['replayed'])
        self.assertTrue(self.runtime().service.process(request,True,False)['replayed'])
        self.assertIsNone(result['file'])
        self.assertFalse((self.root/'fiscal_state.sqlite3').exists())
        self.assertEqual(list(self.agent.glob('*.txt')),[])

    def test_legacy_invalid_flag_does_not_publish(self):
        for valid,refund in ((False,False),(1,False),(True,'false')):
            with self.assertRaises(ValueError): self.runtime().service.process(legacy(),valid,refund)
        self.assertEqual(list(self.agent.glob('*.txt')),[])

    def test_no_legacy_after_state_or_activation_even_if_config_is_old(self):
        for name in ('fiscal_state.sqlite3','fiscal_state.sqlite3-wal','fiscal_state.sqlite3-shm',LOCAL_MARKER):
            path = self.root/name
            path.touch()
            with self.assertRaisesRegex(ValueError,'volver a modo=odoo'):
                self.runtime()
            path.unlink()

    def test_wrong_identity_or_corrupt_activation_cannot_start_local(self):
        FiscalStore.initialize(self.root/'fiscal_state.sqlite3',SCOPE,0,0)
        wrong = config('local'); wrong['FiscalSequence']['odoo_company_id']='19'
        with self.assertRaisesRegex(ValueError,'no corresponde'):
            FiscalRuntime(wrong,self.root,self.agent,self.results)
        (self.root/LOCAL_MARKER).write_text('{}')
        with self.assertRaisesRegex(ValueError,'otro perfil'):
            self.runtime('local')

    def test_legacy_does_not_renumber_or_create_local_state(self):
        runtime = self.runtime()
        answer = runtime.service.process(legacy(),True,False)
        self.assertTrue(answer['pending'])
        self.assertIn('L01|03|01|9981010242|001|0000|',(self.agent/'9981010242.txt').read_text(encoding='cp1252'))
        self.assertFalse((self.root/'fiscal_state.sqlite3').exists())

    def test_legacy_invalid_number_and_local_payload_never_publish(self):
        service = self.runtime().service
        for number in ('10001040043','0000000000','../1','１２３４５６７８９０',123):
            with self.assertRaises(ValueError):
                service.process(legacy(number),True,False)
        for key in ('FISCAL_PROTOCOL','FISCAL_IDENTITY','ORDER_UID','FISCAL_NUMBER'):
            request = legacy(); request[key] = False
            with self.assertRaisesRegex(ValueError,'contador local'):
                service.process(request,True,False)
        self.assertEqual(list(self.agent.glob('*.txt')),[])

    def test_legacy_refund_preserves_existing_type(self):
        service = self.runtime().service
        service.process(legacy(),True,True)
        self.assertIn('L01|03|06|',(self.agent/'9981010242.txt').read_text(encoding='cp1252'))

    def test_legacy_same_number_changed_payload_refused(self):
        service = self.runtime().service
        service.process(legacy(),True,False)
        original = (self.agent/'9981010242.txt').read_bytes()
        changed = legacy(); changed['ORDERLINES'][0]['PRODUCT_NAME']='OTRA VENTA'
        with self.assertRaisesRegex(ValueError,'otro contenido'):
            service.process(changed,True,False)
        with self.assertRaisesRegex(ValueError,'otro contenido'):
            service.process(legacy(),True,True)
        self.assertEqual(original,(self.agent/'9981010242.txt').read_bytes())

    def test_legacy_timeout_restart_does_not_publish_again(self):
        self.runtime().service.process(legacy(),True,False)
        (self.agent/'9981010242.txt').unlink()  # Agente simulado consumió el TXT.
        answer = self.runtime().service.process(legacy(),True,False)
        self.assertTrue(answer['pending'])
        self.assertEqual(list(self.agent.glob('*.txt')),[])

    def test_legacy_late_response_and_cached_reply(self):
        self.runtime().service.process(legacy(),True,False)
        self.result(archived=True)
        runtime = self.runtime()
        answer = runtime.service.process(legacy(),True,False)
        self.assertTrue(answer['procesado'])
        again = runtime.service.process(legacy(),True,False)
        self.assertTrue(again['replayed'])
        self.assertEqual(again['contenido'],answer['contenido'])

    def test_legacy_previous_failure_or_result_is_not_recovered(self):
        service = self.runtime().service
        for folder,name in ((self.agent/'failures','9981010242_000.txt'),(self.results,'result_9981010242_1.txt'),(self.agent/'processing','9981010242.txt')):
            folder.mkdir(exist_ok=True)
            path = folder/name; path.write_text('ANterior')
            with self.assertRaisesRegex(ValueError,'archivos anteriores'):
                service.process(legacy(),True,False)
            self.assertEqual(path.read_text(),'ANterior')
            path.unlink()
        self.assertFalse((self.agent/'9981010242.txt').exists())

    def test_legacy_concurrent_requests_one_publication(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(pool.map(lambda _:self.runtime().service.process(legacy(),True,False),range(8)))
        self.assertTrue(all(answer['pending'] for answer in answers))
        self.assertEqual(len(list(self.agent.glob('*.txt'))),1)

    def test_runtime_new_activation_stops_already_running_legacy(self):
        runtime = self.runtime()
        (self.root/LOCAL_MARKER).write_text('{}')
        with self.assertRaises(ValueError): runtime.require_mode('odoo')
        with self.assertRaises(ValueError): runtime.service.process(legacy(),True,False)

    def test_old_event_rejected_in_local_without_consuming_number(self):
        store = FiscalStore.initialize(self.root/'fiscal_state.sqlite3',SCOPE,100,0)
        server = self.server(self.runtime('local'))
        result = asyncio.run(server.process_new_order_event('test',(legacy(),True,False,True,True)))
        self.assertFalse(result['success'])
        self.assertEqual(store.status()['last_number'],100)
        self.assertEqual(list(self.agent.glob('*.txt')),[])

    def test_local_event_rejected_in_legacy_without_fallback(self):
        server = self.server(self.runtime())
        result = asyncio.run(server.process_local_order_event('test',payload()))
        self.assertFalse(result['procesado'])
        self.assertEqual(result['protocol'],2)
        self.assertEqual(list(self.agent.glob('*.txt')),[])

    def test_old_event_wrapped_and_unwrapped_reply(self):
        server = self.server(self.runtime())
        result = asyncio.run(server.process_new_order_event('test',(legacy(),True,False,True,True)))
        self.assertTrue(result['pending'])
        server.sio.emit.assert_awaited_with('fel_response_event',result,to='test')
        self.result()
        result = asyncio.run(server.process_new_order_event('test',([legacy(),True,False,True,True],)))
        self.assertTrue(result['procesado'])


if __name__ == '__main__': unittest.main()
