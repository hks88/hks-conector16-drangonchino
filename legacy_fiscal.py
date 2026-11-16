"""Protocolo new_order de 5.1.9, sin asignar números ni repetir envíos ambiguos.

El diario solo conserva solicitudes nuevas que entran por esta versión. No
importa, renumera ni reprocesa archivos anteriores de processed/failures.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

from fiscal_sequence import fiscal_number
from local_fiscal import find_result, matching_files, publish_once
import order_process
import order_return


class LegacyFiscalService:
    def __init__(self, base_dir, agent_dir, result_dir, scope, timeout=15):
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "legacy_dispatch.sqlite3"
        self.agent_dir, self.result_dir = Path(agent_dir), Path(result_dir)
        self.scope, self.timeout = dict(scope), timeout
        self.lock = threading.Lock()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE IF NOT EXISTS documents(number TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, document BLOB NOT NULL, state TEXT NOT NULL, response TEXT)")
        return conn

    def _has_previous_files(self, number):
        folders = [self.agent_dir, self.result_dir] + [self.agent_dir / name for name in ("processed", "processing", "failures")]
        return any(
            matching_files(folder, pattern)
            for folder in folders
            for pattern in (number + ".txt", number + "_*.txt", "result_" + number + "_*")
        )

    def process(self, payload, valid, refund):
        from fiscal_runtime import require_legacy_allowed
        require_legacy_allowed(self.base_dir)
        if not isinstance(payload, dict) or type(valid) is not bool or type(refund) is not bool or not valid:
            raise ValueError("Orden heredada no válida; no se generó TXT.")
        if any(key in payload for key in ("FISCAL_PROTOCOL", "FISCAL_IDENTITY", "ORDER_UID", "FISCAL_NUMBER")):
            raise ValueError("Una solicitud del contador local no puede enviarse por new_order.")
        number = fiscal_number(payload.get("ORDER_NUMBER"))
        digest = hashlib.sha256(json.dumps({"data": payload, "refund": refund, "scope": self.scope},
                                sort_keys=True, allow_nan=False, ensure_ascii=True).encode()).hexdigest()
        with self.lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    record = conn.execute("SELECT * FROM documents WHERE number=?", (number,)).fetchone()
                    fresh = record is None
                    if record is None:
                        if self._has_previous_files(number):
                            raise ValueError("El número ya tiene archivos anteriores. Concilie con FF; esta actualización no recupera pendientes ni fallidos.")
                        document = order_process.render_document(payload, valid, refund, fiscal_scope=self.scope)
                        response = {"procesado": True, "sin_documento": True, "success": True, "file": None} if document is None else None
                        conn.execute("INSERT INTO documents VALUES(?,?,?,?,?)", (number, digest,
                                     document.encode("cp1252") if document else b"", "responded" if response else "prepared",
                                     json.dumps(response) if response else None))
                        record = conn.execute("SELECT * FROM documents WHERE number=?", (number,)).fetchone()
                    elif record["payload_hash"] != digest:
                        raise ValueError("Número heredado repetido con otro contenido. No se sobrescribirá ni se renumerará.")
                if record["response"]:
                    return {**json.loads(record["response"]), "replayed": not fresh}
                order_return._registrar_orden_activa(number)
                try:
                    result = find_result(
                        number,
                        self.agent_dir,
                        self.result_dir,
                        0,
                        search_archive=record["state"] != "prepared",
                    )
                    with conn:
                        claimed = conn.execute("UPDATE documents SET state='dispatched' WHERE number=? AND state='prepared'", (number,)).rowcount == 1
                    if result is None and claimed:
                        require_legacy_allowed(self.base_dir)
                        try:
                            publish_once(self.agent_dir / (number + ".txt"), record["document"])
                        except OSError:
                            return {"procesado": False, "pending": True, "mensaje": "Publicación no confirmada. Conserve el pedido y concilie con FF; no se reenviará automáticamente."}
                    if result is None:
                        result = find_result(
                            number,
                            self.agent_dir,
                            self.result_dir,
                            self.timeout,
                            search_archive=False,
                        )
                    if result is None:
                        return {"procesado": False, "pending": True, "mensaje": "Respuesta FF pendiente. No se generará otro número ni otro TXT."}
                    with conn:
                        conn.execute("UPDATE documents SET state='responded',response=? WHERE number=? AND response IS NULL", (json.dumps(result), number))
                    return {**result, "replayed": False}
                finally:
                    order_return._liberar_orden_activa(number)
            finally:
                conn.close()
