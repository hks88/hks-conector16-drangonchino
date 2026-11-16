"""Estado fiscal local. La inicialización es explícita; nunca se crea al facturar."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

MAX_NUMBER = 9999999999
SCHEMA_VERSION = "2"
PROTOCOL_VERSION = 2


def fiscal_number(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{10}", value) or int(value) == 0:
        raise ValueError("El número fiscal debe tener exactamente 10 dígitos, de 0000000001 a 9999999999")
    return value


def scope(emisor: str, ambiente: str, sucursal: str, punto: str,
          odoo_database: str, odoo_company_id: str, odoo_pos_config_id: str) -> dict:
    if not isinstance(emisor, str) or not emisor.strip() or any(c in emisor for c in "\r\n|"):
        raise ValueError("Configure el RUC emisor antes de inicializar la secuencia")
    if ambiente not in ("1", "2") or not isinstance(sucursal, str) or not re.fullmatch(r"[A-Z0-9]{4}", sucursal):
        raise ValueError("Ambiente debe ser 1/2 y sucursal debe tener 4 caracteres en mayúscula (A-Z o 0-9)")
    if not isinstance(punto, str) or not re.fullmatch(r"[0-9]{3}", punto) or punto == "000":
        raise ValueError("Punto fiscal debe estar entre 001 y 999")
    if not isinstance(odoo_database, str) or not odoo_database.strip() or any(c in odoo_database for c in "\r\n|"):
        raise ValueError("Configure odoo_database con el nombre exacto de la base Odoo")
    for label, value in (("odoo_company_id", odoo_company_id), ("odoo_pos_config_id", odoo_pos_config_id)):
        if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
            raise ValueError(f"Configure {label} con el ID interno positivo de Odoo, no POS/xxxxx")
    return dict(emisor=emisor.strip(), ambiente=ambiente, sucursal=sucursal, punto=punto,
                odoo_database=odoo_database.strip(), odoo_company_id=odoo_company_id,
                odoo_pos_config_id=odoo_pos_config_id)


def scope_from_config(config) -> dict:
    if config.get("FiscalSequence", "modo", fallback="").strip() != "local":
        raise ValueError("El contador requiere FiscalSequence.modo=local. La ruta heredada no utiliza esta utilidad.")
    return scope(config.get("FiscalSequence", "emisor", fallback=""),
                 config.get("FiscalSequence", "ambiente", fallback=""),
                 config.get("Target", "sucursal", fallback=""),
                 config.get("Target", "punto_de_facturacion", fallback=""),
                 config.get("FiscalSequence", "odoo_database", fallback=""),
                 config.get("FiscalSequence", "odoo_company_id", fallback=""),
                 config.get("FiscalSequence", "odoo_pos_config_id", fallback=""))


class FiscalStore:
    def __init__(self, path: Path, expected_scope: dict):
        self.path = Path(path).resolve()
        self.scope = scope(**expected_scope)

    @contextmanager
    def connect(self):
        if not self.path.is_file():
            raise ValueError("Falta fiscal_state.sqlite3. No se reiniciará el contador. Inicialice solo tras conciliar FF o recupere el estado con soporte.")
        conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA synchronous=FULL")
            actual = dict(conn.execute("SELECT key,value FROM metadata"))
            if actual != {**self.scope, "schema_version": SCHEMA_VERSION}:
                raise ValueError("El estado fiscal no corresponde al emisor/base/compañía/TPV/ambiente/sucursal/punto o a esta versión. No borre ni reinicialice el archivo; concilie con soporte.")
            yield conn
        finally:
            conn.close()

    @classmethod
    def initialize(cls, path: Path, expected_scope: dict, last_invoice: int, last_credit: int):
        normalized = scope(**expected_scope)
        for number in (last_invoice, last_credit):
            if type(number) is not int or not 0 <= number <= MAX_NUMBER:
                raise ValueError("Últimos números fuera del rango fiscal")
        target = Path(path).resolve()
        if (target.parent / "fiscal_local_activated.json").exists():
            raise ValueError("Esta PC ya activó el contador local. No se permite inicializarlo de nuevo aunque falte el SQLite; recupere su estado con soporte.")
        # Exclusivo: ni una reinstalación ni un segundo operador pueden resetearlo.
        with target.open("xb"):
            pass
        conn = sqlite3.connect(target)
        try:
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript("""
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE counter(id INTEGER PRIMARY KEY CHECK(id=1), last_number INTEGER NOT NULL CHECK(last_number BETWEEN 0 AND 9999999999));
                CREATE TABLE documents(
                    order_uid TEXT PRIMARY KEY,
                    number TEXT UNIQUE CHECK(number IS NULL OR length(number)=10),
                    doc_type TEXT NOT NULL CHECK(doc_type IN ('00','01','06','09')),
                    payload_hash TEXT NOT NULL,
                    document BLOB NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('prepared','dispatched','responded')),
                    response TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE initialization(last_invoice INTEGER NOT NULL, last_credit INTEGER NOT NULL);
            """)
            with conn:
                conn.executemany("INSERT INTO metadata VALUES(?,?)", {**normalized, "schema_version": SCHEMA_VERSION}.items())
                # Contador común: no colisionan nombres TXT entre facturas y NC.
                conn.execute("INSERT INTO counter VALUES(1,?)", (max(last_invoice, last_credit),))
                conn.execute("INSERT INTO initialization VALUES(?,?)", (last_invoice, last_credit))
        finally:
            conn.close()
        return cls(target, normalized)

    def prepare(self, payload: dict, renderer) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("La solicitud fiscal debe ser un objeto")
        uid = payload.get("ORDER_UID")
        if not isinstance(uid, str) or not re.fullmatch(r"[a-f0-9]{32}", uid):
            raise ValueError("Falta ORDER_UID estable: actualice el módulo fiscal para usar el contador local")
        if type(payload.get("FISCAL_PROTOCOL")) is not int or payload["FISCAL_PROTOCOL"] != PROTOCOL_VERSION or type(payload.get("IS_REFUND")) is not bool:
            raise ValueError("Protocolo fiscal local incompatible: use módulo Odoo 4.1.5 y conector 5.2.8 juntos")
        identity = payload.get("FISCAL_IDENTITY")
        if not isinstance(identity, dict) or set(identity) != set(self.scope):
            raise ValueError("Falta la identidad fiscal completa del TPV. Actualice y configure el módulo Odoo 4.1.5")
        mismatches = [key for key, value in self.scope.items() if identity[key] != value]
        if mismatches:
            raise ValueError("Venta de otra compañía/emisor o configuración fiscal distinta a esta PC (" + ", ".join(mismatches) + "). No se reservó un número para esta solicitud; use la PC correspondiente.")
        expected_number = payload.get("FISCAL_NUMBER")
        if expected_number:
            fiscal_number(expected_number)
        canonical = {key: value for key, value in payload.items() if key != "FISCAL_NUMBER"}
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
        with self.connect() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            found = conn.execute("SELECT * FROM documents WHERE order_uid=?", (uid,)).fetchone()
            if found:
                if expected_number and found["number"] != expected_number:
                    raise ValueError("El número guardado en Odoo difiere del registro local; detenga y concilie")
                if found["payload_hash"] != digest:
                    if found["state"] != "prepared" or not found["number"]:
                        raise ValueError("La venta ya fue enviada o respondida y su contenido cambió. No se emitirá otro documento; concilie con FF.")
                    # Antes de claim_dispatch no pudo publicarse el TXT. Se
                    # permite corregir únicamente pagos, conservando UID,
                    # número, productos, cliente, totales y tipo fiscal.
                    amended = renderer(
                        {**payload, "ORDER_NUMBER": found["number"]},
                        True,
                        payload["IS_REFUND"],
                    )
                    if not isinstance(amended, str) or not amended:
                        raise ValueError("La corrección no produce un documento fiscal; se conservó la solicitud original")
                    header = amended.split("|", 6)
                    allowed_types = ("06", "09") if payload["IS_REFUND"] else ("01",)
                    if (len(header) < 7 or header[0] != "L01" or header[1] not in ("01", "03") or
                            header[2] not in allowed_types or
                            header[3:6] != [found["number"], self.scope["punto"], self.scope["sucursal"]]):
                        raise ValueError("La corrección no conserva número/tipo/sucursal/punto fiscal")

                    original_text = bytes(found["document"]).decode("cp1252")
                    original_lines = original_text.splitlines()
                    amended_lines = amended.splitlines()
                    if not original_lines or not amended_lines:
                        raise ValueError("La corrección no conserva el documento fiscal preparado")
                    original_header = original_lines[0].split("|")
                    amended_header = amended_lines[0].split("|")
                    if (len(original_header) < 8 or len(original_header) != len(amended_header) or
                            original_header[:6] != amended_header[:6] or
                            original_header[7:] != amended_header[7:]):
                        raise ValueError(
                            "La corrección cambió la cabecera fiscal además de la hora regenerada"
                        )
                    # Re-rendering recalculates fecha_emision. A prepared TXT has
                    # never been published, but keeping its original timestamp
                    # makes the correction deterministic and leaves every other
                    # header field under exact comparison.
                    amended_header[6] = original_header[6]
                    amended_lines[0] = "|".join(amended_header)
                    amended = "\n".join(amended_lines) + ("\n" if amended.endswith("\n") else "")
                    original_non_payment = [
                        line for line in original_lines
                        if not line.startswith("L08|")
                    ]
                    amended_non_payment = [
                        line for line in amended_lines
                        if not line.startswith("L08|")
                    ]
                    if original_non_payment != amended_non_payment:
                        raise ValueError(
                            "Solo puede corregir los métodos o importes de pago antes de enviar a FF. "
                            "Productos, cliente, totales y tipo fiscal deben conservarse."
                        )
                    conn.execute(
                        "UPDATE documents SET payload_hash=?,document=?,doc_type=? "
                        "WHERE order_uid=? AND state='prepared'",
                        (digest, amended.encode("cp1252"), header[2], uid),
                    )
                    updated = dict(conn.execute(
                        "SELECT * FROM documents WHERE order_uid=?", (uid,)
                    ).fetchone())
                    updated["amended_before_dispatch"] = True
                    return updated
                return dict(found)
            if expected_number:
                raise ValueError("Odoo tiene un número fiscal sin registro local. No se renumerará; revise el respaldo del contador.")
            previous = conn.execute("SELECT last_number FROM counter WHERE id=1").fetchone()[0]
            lines = payload.get("ORDERLINES")
            only_abono = isinstance(lines, list) and bool(lines) and all(
                isinstance(line, dict) and line.get("PRODUCT_NAME") == "Abono" for line in lines)
            if previous >= MAX_NUMBER and not only_abono:
                raise ValueError("Rango fiscal agotado. Coordine otra serie con FF; no reinicie este contador.")
            number = fiscal_number(f"{min(previous + 1, MAX_NUMBER):010d}")
            request = {**payload, "ORDER_NUMBER": number}
            document = renderer(request, True, payload["IS_REFUND"])
            if only_abono and document is None:
                # Conserva la política del módulo: Abono solo no genera TXT.
                # Registra la solicitud para que no cambie después del ACK.
                conn.execute("INSERT INTO documents(order_uid,number,doc_type,payload_hash,document,state,response) VALUES(?,NULL,'00',?,?,'responded',?)",
                             (uid, digest, b"", json.dumps({"procesado": True, "sin_documento": True})))
                return dict(conn.execute("SELECT * FROM documents WHERE order_uid=?", (uid,)).fetchone())
            if not isinstance(document, str) or not document:
                raise ValueError("La operación no produce un documento fiscal; no se reservó número")
            header = document.split("|", 6)
            allowed_types = ("06", "09") if payload["IS_REFUND"] else ("01",)
            if (len(header) < 7 or header[0] != "L01" or header[1] not in ("01", "03") or
                    header[2] not in allowed_types or header[3:6] != [number, self.scope["punto"], self.scope["sucursal"]]):
                raise ValueError("La cabecera TXT no coincide con el número/tipo/sucursal/punto del estado fiscal")
            encoded = document.encode("cp1252")
            conn.execute("INSERT INTO documents(order_uid,number,doc_type,payload_hash,document,state) VALUES(?,?,?,?,?,'prepared')",
                         (uid, number, header[2], digest, encoded))
            conn.execute("UPDATE counter SET last_number=? WHERE id=1", (int(number),))
            return dict(conn.execute("SELECT * FROM documents WHERE order_uid=?", (uid,)).fetchone())

    def claim_dispatch(self, uid: str) -> bool:
        # Commit ANTES de publicar. Si el proceso se interrumpe, no se reenvía
        # automáticamente un documento cuyo estado en FF es desconocido.
        with self.connect() as conn, conn:
            cursor = conn.execute("UPDATE documents SET state='dispatched' WHERE order_uid=? AND state='prepared'", (uid,))
            return cursor.rowcount == 1

    def save_response(self, uid: str, response: dict):
        with self.connect() as conn, conn:
            conn.execute("UPDATE documents SET state='responded',response=? WHERE order_uid=? AND response IS NULL",
                         (json.dumps(response, ensure_ascii=True), uid))

    def order_status(self, uid: str) -> dict:
        """Return the authoritative retry state without reserving or publishing."""
        if not isinstance(uid, str) or not re.fullmatch(r"[a-f0-9]{32}", uid):
            raise ValueError("ORDER_UID inválido para consultar el estado fiscal")
        with self.connect() as conn:
            found = conn.execute(
                "SELECT order_uid,number,state FROM documents WHERE order_uid=?",
                (uid,),
            ).fetchone()
        if not found:
            return {
                "order_uid": uid,
                "state": "missing",
                "fiscal_number": None,
                "request_mutable": True,
            }
        state = found["state"]
        return {
            "order_uid": found["order_uid"],
            "state": state,
            "fiscal_number": found["number"],
            # prepared is committed before publication and claim_dispatch moves
            # it to dispatched before the TXT can be exposed to Agente FF.
            "request_mutable": state == "prepared",
        }

    def status(self) -> dict:
        with self.connect() as conn:
            return {**self.scope, "last_number": conn.execute("SELECT last_number FROM counter").fetchone()[0],
                    "states": dict(conn.execute("SELECT state,count(*) FROM documents GROUP BY state"))}

    def backup(self, destination: Path):
        target = Path(destination).resolve()
        with target.open("xb"):
            pass
        with self.connect() as conn:
            copy = sqlite3.connect(target)
            try:
                conn.backup(copy)
            finally:
                copy.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Administración de secuencia fiscal local; nunca emite documentos")
    parser.add_argument("action", choices=("init", "status", "backup"))
    parser.add_argument("--config", default="config.ini")
    parser.add_argument("--last-invoice", type=int)
    parser.add_argument("--last-credit", type=int, help="Mayor último número confirmado entre NC/devoluciones (incluidos tipos 06 y 09 del flujo existente)")
    parser.add_argument("--confirm-ff-and-queues", action="store_true")
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args(argv)
    import configparser
    config_path = Path(args.config).resolve()
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_path, encoding="utf-8-sig")
    expected = scope_from_config(config)
    path = config_path.parent / "fiscal_state.sqlite3"
    if args.action == "init":
        if not args.confirm_ff_and_queues or args.last_invoice is None or args.last_credit is None:
            parser.error("Indique ambos últimos números y --confirm-ff-and-queues solo después de conciliarlos con FF y las colas")
        store = FiscalStore.initialize(path, expected, args.last_invoice, args.last_credit)
    else:
        store = FiscalStore(path, expected)
    if args.action == "backup":
        if args.destination is None:
            parser.error("Falta --destination; no se sobrescriben respaldos")
        store.backup(args.destination)
    print(json.dumps(store.status(), indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
