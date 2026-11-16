"""Prueba el EXE de administración con estado ficticio, sin Agente FF ni ventas."""
import configparser
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_compatibility import config
from fiscal_runtime import FiscalRuntime, LOCAL_MARKER

root=ROOT/'tests/artifacts'/('cli-'+uuid.uuid4().hex)
root.mkdir(parents=True,exist_ok=False)
ini=root/'config.ini'
test_config = config('local')
test_config['Target']['sucursal'] = '4B00'
with ini.open('w',encoding='utf-8') as stream:test_config.write(stream)
exe=ROOT/'dist/SecuenciaFiscal.exe'
def run(*args,success=True,config_path=ini):
    result=subprocess.run([str(exe),*args,'--config',str(config_path)],cwd=root,
                          capture_output=True,text=True,timeout=30,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    assert (result.returncode==0)==success,(args,result.returncode,result.stdout,result.stderr)
    return json.loads(result.stdout) if success else result.stderr

assert 'Falta fiscal_state' in run('status',success=False)
assert not (root/'fiscal_state.sqlite3').exists()
first=run('init','--last-invoice','12','--last-credit','9','--confirm-ff-and-queues')
assert first['last_number']==12 and first['states']=={} and first['sucursal']=='4B00'
assert run('status')['last_number']==12 and run('status')['sucursal']=='4B00'
destination=root/'backup.sqlite3'
run('backup','--destination',str(destination))
with sqlite3.connect(destination.as_uri()+'?mode=ro',uri=True) as db:
    assert db.execute('SELECT last_number FROM counter').fetchone()[0]==12
run('init','--last-invoice','0','--last-credit','0','--confirm-ff-and-queues',success=False)
runtime=FiscalRuntime(test_config,root,root/'agent',root/'agent/Resultado_FF',timeout=0)
assert (root/LOCAL_MARKER).exists()
lost=root/'sqlite-perdido-simulado';lost.mkdir()
(lost/'config.ini').write_bytes(ini.read_bytes())
(lost/LOCAL_MARKER).write_bytes((root/LOCAL_MARKER).read_bytes())
error=run('init','--last-invoice','0','--last-credit','0','--confirm-ff-and-queues',success=False,config_path=lost/'config.ini')
assert not (lost/'fiscal_state.sqlite3').exists()
assert 'contador local' in error
assert run('status')['last_number']==12
report={'success':True,'executable':'SecuenciaFiscal.exe','scope':'EXE real, sucursal 4B00, perfil y cortes SIMULADOS; no Agente ni TXT',
        'checks':['estado ausente no se crea','inicialización explícita','status','backup SQLite',
                  'segunda inicialización bloqueada','marca de activación bloquea init sin SQLite','contador intacto'],
        'last_number':12,'test_root':str(root)}
(ROOT/'test-packaged-sequence-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))
