"""Socket.IO real de 5.1.9 y 5.2.8 con Agente FF SIMULADO.

Importa funciones del código original sin ejecutar interfaz, watcher ni main.
Todos los TXT/respuestas viven en temporales no vigilados por el Agente real.
No usa 3222, no detiene procesos ni cambia configuración instalada.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
from urllib.error import URLError
from urllib.request import Request, urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import socketio
from hypercorn.asyncio import serve
from hypercorn.config import Config
import frontend
from fiscal_runtime import FiscalRuntime
from test_compatibility import config, legacy


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def actual_519():
    old = ROOT / 'tests/fixtures/connector-5.1.9'
    renderer = load('original_519_renderer',old/'order_process.py')
    result = load('original_519_results',old/'order_return.py')
    with patch.dict(sys.modules,{'order_process':renderer,'order_return':result}):
        bridge = load('original_519_frontend',old/'frontend.py')
    return bridge, renderer, result


async def run_case(version):
    stop = asyncio.Event()
    with tempfile.TemporaryDirectory(prefix='ff_legacy_SIMULADO_') as directory:
        root = Path(directory); agent=root/'agent'; agent.mkdir()
        branch = '0000' if version == '5.1.9' else '4B00'
        if version == '5.1.9':
            source, renderer, result = actual_519()
            renderer.AGENT_DIR=agent; renderer.n_sucursal='0000'; renderer.punto_facturacion='001'
            result.AGENT_DIR=agent; result.RESULT_DIR=agent/'Resultado_FF'
            bridge=source.Server.__new__(source.Server)
        else:
            bridge=frontend.Server.__new__(frontend.Server)
            current_config = config()
            current_config['Target']['sucursal'] = branch
            bridge.runtime=FiscalRuntime(current_config,root,agent,agent/'Resultado_FF',timeout=5)
        bridge.open_drawer=lambda *_:False
        sio=socketio.AsyncServer(async_mode='asgi',logger=False,engineio_logger=False)
        bridge.sio=sio

        @sio.event
        async def new_order(sid,*args):
            return await bridge.process_new_order_event(sid,args)

        published=set()
        async def simulated_agent():
            while not stop.is_set():
                for path in agent.glob('[0-9]*.txt'):
                    if path.name in published: continue
                    # Comprobar número inalterado, tipo y punto antes de simular FF.
                    fields=path.read_text(encoding='cp1252').split('|',6)
                    assert fields[3] == path.stem and len(fields[3]) == 10
                    assert fields[4:6] == ['001',branch]
                    assert fields[2] == ('06' if path.stem.endswith('43') else '01')
                    published.add(path.name)
                    (agent/('result_'+path.stem+'_1.txt')).write_text(
                        'CUFE-SIMULADO-NO-FISCAL\nhttps://example.test/cafe\n2026-08-28T11:00:00-05:00\n')
                await asyncio.sleep(0.01)

        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]
        http_config=Config(); http_config.bind=[f'127.0.0.1:{port}']; http_config.lifespan='off'; http_config.errorlog=None
        server=asyncio.create_task(serve(socketio.ASGIApp(sio),http_config,shutdown_trigger=stop.wait))
        fake=asyncio.create_task(simulated_agent())
        base=f'http://127.0.0.1:{port}/socket.io/?EIO=4&transport=polling'
        def http(url,data=None):
            req=Request(url,data=data.encode() if data is not None else None,headers={'Content-Type':'text/plain;charset=UTF-8'})
            with urlopen(req,timeout=10) as response:return response.read().decode()
        try:
            for _ in range(50):
                try:
                    first=await asyncio.to_thread(http,base)
                    break
                except URLError: await asyncio.sleep(0.02)
            else: raise AssertionError('Servidor de prueba no iniciado')
            url=base+'&sid='+json.loads(first[1:])['sid']
            await asyncio.to_thread(http,url,'40'); await asyncio.to_thread(http,url)
            async def call(number,refund,ack):
                event=['new_order',legacy(number),True,refund,True,True]
                await asyncio.to_thread(http,url,'42'+str(ack)+json.dumps(event))
                for _ in range(5):
                    packet=await asyncio.to_thread(http,url)
                    for item in packet.split('\x1e'):
                        prefix='43'+str(ack)
                        if item.startswith(prefix):return json.loads(item[len(prefix):])[0]
                raise AssertionError('No ACK para la solicitud')
            invoice=await call('9981010242',False,1)
            credit=await call('9981010243',True,2)
            assert invoice['procesado'] and credit['procesado'],(invoice,credit)
            assert invoice['contenido']['identificador']=='CUFE-SIMULADO-NO-FISCAL'
            assert len(published)==2,published
            if version=='5.2.8':
                repeated=await call('9981010242',False,3)
                assert repeated['replayed'] and len(published)==2,repeated
                invalid=await call('10001040043',False,4)
                assert invalid['success'] is False and not (agent/'10001040043.txt').exists(),invalid
            await asyncio.to_thread(http,url,'41')
            return {'version':version,'branch':branch,'transport':'Socket.IO HTTP real','agent':'SIMULADO','invoices':1,'credits':1,'txt_count':len(published),'hardware':False}
        finally:
            stop.set(); await asyncio.wait_for(server,10); await fake


async def main():
    results=[]
    for version in ('5.1.9','5.2.8'):
        results.append(await run_case(version))
    print(json.dumps(results,ensure_ascii=True,indent=2))


if __name__=='__main__':asyncio.run(main())
