"""Prueba real de Socket.IO en loopback, con Agente FF SIMULADO y carpetas temporales."""
import asyncio
import json
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import AsyncMock, patch
from urllib.request import Request, urlopen
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import socketio
from hypercorn.asyncio import serve
from hypercorn.config import Config
import frontend
import order_process
from fiscal_sequence import FiscalStore
from local_fiscal import LocalFiscalService
from fiscal_runtime import FiscalRuntime
from test_compatibility import config as fiscal_config
from test_fiscal_sequence import SCOPE, payload

TEST_SCOPE = {**SCOPE, "sucursal": "4B00"}


def test_payload(index=1):
    request = payload(index)
    request["FISCAL_IDENTITY"] = dict(TEST_SCOPE)
    return request


async def main():
    stop = asyncio.Event()
    with tempfile.TemporaryDirectory(prefix="ff_SIMULADO_") as directory:
        root = Path(directory)
        agent = root / "agent"
        agent.mkdir()
        store = FiscalStore.initialize(root / "fiscal_state.sqlite3", TEST_SCOPE, 9981010242, 9517020262)
        order_process.n_sucursal, order_process.punto_facturacion = "4B00", "001"
        bridge = frontend.Server.__new__(frontend.Server)
        runtime_config = fiscal_config('local')
        runtime_config['Target']['sucursal'] = '4B00'
        bridge.runtime = FiscalRuntime(runtime_config, root, agent, agent / 'Resultado_FF')
        bridge.open_drawer = lambda *_: True  # No usa hardware.
        sio = socketio.AsyncServer(async_mode="asgi", logger=False, engineio_logger=False)
        bridge.sio = sio

        @sio.event
        async def new_order_local(sid, request):
            return await bridge.process_local_order_event(sid, request)

        @sio.event
        async def fiscal_request_status(sid, request):
            return await bridge.process_fiscal_status_event(sid, request)

        published = set()

        async def simulated_agent():
            while not stop.is_set():
                for txt in agent.glob("[0-9]*.txt"):
                    if txt.name not in published:
                        published.add(txt.name)
                        (agent / f"result_{txt.stem}_1.txt").write_text(
                            "CUFE-SIMULADO-NO-FISCAL\nhttps://example.test/cafe\n2026-08-27T12:00:00-05:00\n", encoding="utf-8")
                await asyncio.sleep(0.02)

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config = Config()
        config.bind = [f"127.0.0.1:{port}"]
        config.lifespan = "off"
        config.errorlog = None
        task = asyncio.create_task(serve(socketio.ASGIApp(sio), config, shutdown_trigger=stop.wait))
        fake = asyncio.create_task(simulated_agent())
        base = f"http://127.0.0.1:{port}/socket.io/?EIO=4&transport=polling"

        def http(url, data=None):
            request = Request(url, data=data.encode() if data is not None else None,
                              headers={"Content-Type": "text/plain;charset=UTF-8"})
            with urlopen(request, timeout=5) as response:
                return response.read().decode()

        try:
            for attempt in range(40):
                try:
                    handshake = await asyncio.to_thread(http, base)
                    break
                except URLError:
                    await asyncio.sleep(0.025)
            else:
                raise RuntimeError("No se inició el servidor de prueba")
            sid = json.loads(handshake[1:])["sid"]
            url = base + "&sid=" + sid
            await asyncio.to_thread(http, url, "40")
            await asyncio.to_thread(http, url)

            async def send_event(event, request, ack):
                await asyncio.to_thread(http, url, f"42{ack}" + json.dumps([event, request]))
                packet = await asyncio.to_thread(http, url)
                prefix = f"43{ack}"
                answer = next(item for item in packet.split("\x1e") if item.startswith(prefix))
                return json.loads(answer[len(prefix):])[0]

            async def send(request, ack):
                return await send_event("new_order_local", request, ack)

            status_request = {
                "FISCAL_PROTOCOL": 2,
                "FISCAL_IDENTITY": dict(TEST_SCOPE),
                "ORDER_UID": test_payload()["ORDER_UID"],
            }
            missing = await send_event("fiscal_request_status", status_request, 10)
            assert missing["state"] == "missing" and missing["request_mutable"], missing
            assert store.status()["last_number"] == 9981010242
            first = await send(test_payload(), 1)
            assert first["procesado"] and first["fiscal_number"] == "9981010243", first
            assert first['protocol'] == 2 and first['fiscal_identity'] == TEST_SCOPE, first
            repeat = await send({**test_payload(), "FISCAL_NUMBER": first["fiscal_number"]}, 2)
            assert repeat["procesado"] and repeat["replayed"], repeat
            responded = await send_event("fiscal_request_status", status_request, 11)
            assert responded["state"] == "responded" and not responded["request_mutable"], responded
            second = await send(test_payload(2), 3)
            assert second["fiscal_number"] == "9981010244" and second["procesado"], second
            assert len(published) == 2, published
            assert store.status()["last_number"] == 9981010244
            # Mismo punto 4B00/001, pero emisor/compañía de otra PC.
            wrong = test_payload(3)
            wrong['FISCAL_IDENTITY'] = {**TEST_SCOPE, 'emisor': 'RUC-OTRA-PC', 'odoo_company_id': '19'}
            refused = await send(wrong, 4)
            assert not refused['procesado'] and 'otra compañía/emisor' in refused['mensaje'], refused
            assert not refused.get('fiscal_number'), refused
            old = await send({**test_payload(4), 'FISCAL_PROTOCOL': 1}, 5)
            assert not old['procesado'] and 'incompatible' in old['mensaje'], old
            # Ni configurar accidentalmente modo=odoo permite saltar la validación.
            with patch.object(bridge, 'sio', AsyncMock()), patch.object(order_process, 'order') as legacy_writer:
                rejected_legacy = await bridge.process_new_order_event('legacy-test', ({'ORDER_NUMBER':'0000000001'}, True, False, True, True))
                assert not rejected_legacy['success'], rejected_legacy
                legacy_writer.assert_not_called()
            assert len(published) == 2 and store.status()['last_number'] == 9981010244
            await asyncio.to_thread(http, url, "41")
            print("OK: Socket.IO real/HTTP loopback con sucursal 4B00, consulta UID read-only, 2 ventas, reintento sin duplicado, otra compañía y protocolos anteriores rechazados sin TXT/consumo. Agente SIMULADO; ninguna factura real.")
        finally:
            stop.set()
            await asyncio.wait_for(task, 10)
            await fake


if __name__ == "__main__":
    asyncio.run(main())
