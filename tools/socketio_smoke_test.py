from __future__ import annotations

import sys

import socketio


URL = "http://127.0.0.1:3222"


def main() -> int:
    client = socketio.Client(logger=False, engineio_logger=False)
    try:
        client.connect(URL, transports=["polling", "websocket"])
        print(f"[OK] Socket.IO conectado a {URL}")
        print(f"[OK] SID: {client.sid}")
        return 0
    except Exception as exc:
        print(f"[ERROR] No fue posible conectar a {URL}: {exc}")
        return 1
    finally:
        if client.connected:
            client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
