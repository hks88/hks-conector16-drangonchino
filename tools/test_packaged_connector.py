"""Prueba el EXE real recuperando una respuesta archivada simulada.

Usa un directorio temporal, SQLite y respuesta ficticios. No abre el Agente FF,
no usa Odoo/PAC y no publica ningún TXT fiscal.
"""
from __future__ import annotations

import json
import ctypes
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from fiscal_sequence import FiscalStore  # noqa: E402
import order_process  # noqa: E402
from test_compatibility import config  # noqa: E402
from test_fiscal_sequence import SCOPE, payload  # noqa: E402

TEST_SCOPE = {**SCOPE, "sucursal": "4B00"}


def port_open() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", 3222)) == 0


def port_owner():
    result = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True, timeout=10
    )
    for line in result.stdout.splitlines():
        fields = line.split()
        if (len(fields) == 5 and fields[0] == "TCP" and
                fields[1] == "127.0.0.1:3222" and fields[3] == "LISTENING"):
            return int(fields[4])
    return None


def terminate_pid(pid):
    process_terminate = 0x0001
    handle = ctypes.windll.kernel32.OpenProcess(process_terminate, False, pid)
    if handle:
        try:
            ctypes.windll.kernel32.TerminateProcess(handle, 0)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)


if port_open():
    raise RuntimeError("El puerto 3222 ya está ocupado; no se ejecutó la prueba aislada")

with tempfile.TemporaryDirectory(prefix="ff_exe_SIMULADO_") as directory:
    test_root = Path(directory)
    executable = test_root / "Conector_V5.2.8.exe"
    shutil.copy2(ROOT / "dist" / executable.name, executable)
    test_config = config("local")
    test_config["Target"]["sucursal"] = "4B00"
    with (test_root / "config.ini").open("w", encoding="utf-8") as stream:
        test_config.write(stream)

    agent = test_root / "Agente_FF"
    archived = agent / "Resultado_FF"
    archived.mkdir(parents=True)
    store = FiscalStore.initialize(
        test_root / "fiscal_state.sqlite3", TEST_SCOPE, 9981010242, 9517020262
    )
    request = payload()
    request["FISCAL_IDENTITY"] = dict(TEST_SCOPE)
    record = store.prepare(
        request,
        lambda data, valid, refund: order_process.render_document(
            data, valid, refund, fiscal_scope=TEST_SCOPE
        ),
    )
    assert record["number"] == "9981010243"
    assert store.claim_dispatch(record["order_uid"])
    response_path = archived / "result_9981010243_1.txt"
    response_path.write_text(
        "CUFE-SIMULADO-EXE\nhttps://example.test/cafe\n2026-08-29T12:00:00-05:00\n",
        encoding="utf-8",
    )

    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen(
        [str(executable)],
        cwd=test_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        startupinfo=startup,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    engine_url = None
    listener_pid = None

    def http(url, data=None):
        web_request = Request(
            url,
            data=data.encode() if data is not None else None,
            headers={"Content-Type": "text/plain;charset=UTF-8"},
        )
        with urlopen(web_request, timeout=15) as response:
            return response.read().decode()

    def call(event, data, ack):
        http(engine_url, "42" + str(ack) + json.dumps([event, data]))
        prefix = "43" + str(ack)
        for _ in range(10):
            packet = http(engine_url)
            for item in packet.split("\x1e"):
                if item.startswith(prefix):
                    return json.loads(item[len(prefix):])[0]
        raise RuntimeError("El EXE no devolvió ACK Socket.IO")

    try:
        for _ in range(120):
            if port_open():
                listener_pid = port_owner()
                if listener_pid is None:
                    raise RuntimeError("No se pudo identificar el proceso aislado en 3222")
                break
            if process.poll() is not None:
                raise RuntimeError("El EXE terminó antes de abrir Socket.IO")
            time.sleep(0.1)
        else:
            raise RuntimeError("El EXE no abrió Socket.IO en 12 segundos")

        base_url = "http://127.0.0.1:3222/socket.io/?EIO=4&transport=polling"
        handshake = http(base_url)
        engine_url = base_url + "&sid=" + json.loads(handshake[1:])["sid"]
        http(engine_url, "40")
        http(engine_url)
        missing_status = call(
            "fiscal_request_status",
            {
                "FISCAL_PROTOCOL": 2,
                "FISCAL_IDENTITY": dict(TEST_SCOPE),
                "ORDER_UID": "f" * 32,
            },
            1,
        )
        assert missing_status["state"] == "missing", missing_status
        assert missing_status["request_mutable"] is True, missing_status
        dispatched_status = call(
            "fiscal_request_status",
            {
                "FISCAL_PROTOCOL": 2,
                "FISCAL_IDENTITY": dict(TEST_SCOPE),
                "ORDER_UID": request["ORDER_UID"],
            },
            2,
        )
        assert dispatched_status["state"] == "dispatched", dispatched_status
        assert dispatched_status["request_mutable"] is False, dispatched_status
        answer = call("new_order_local", request, 3)
        assert answer["procesado"] is True, answer
        assert answer["fiscal_number"] == "9981010243", answer
        assert answer["contenido"]["identificador"] == "CUFE-SIMULADO-EXE", answer
        assert not (agent / "9981010243.txt").exists(), "El EXE repitió el TXT"
        assert store.status()["states"] == {"responded": 1}, store.status()
        log_text = (test_root / "log.txt").read_text(encoding="utf-8", errors="replace")
        assert "versión=5.2.8" in log_text
        assert "win32timezone" not in log_text
        report = {
            "success": True,
            "executable": executable.name,
            "scope": "EXE real con sucursal 4B00; SQLite y respuesta archivada simulados; sin Odoo, Agente FF, PAC ni TXT nuevo",
            "recovered_number": "9981010243",
            "response_state": store.status()["states"],
            "txt_republished": False,
            "win32timezone_error": False,
            "read_only_status_query": {
                "missing_mutable": True,
                "dispatched_mutable": False,
            },
        }
        (ROOT / "test-packaged-connector-results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        if engine_url is not None:
            try:
                http(engine_url, "41")
            except Exception:
                pass
        for pid in dict.fromkeys(pid for pid in (listener_pid, process.pid) if pid):
            terminate_pid(pid)
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        for _ in range(50):
            if not port_open():
                break
            time.sleep(0.1)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.5)
