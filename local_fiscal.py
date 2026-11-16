"""Coordina un único punto fiscal local sin reenvíos automáticos ambiguos."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time

try:
    import win32file
    # FindFilesW convierte FILETIME con este módulo de pywin32. Importarlo de
    # forma explícita permite a PyInstaller detectarlo y evita un fallo tardío
    # durante la primera venta.
    import win32timezone  # noqa: F401
except ImportError:  # Permite ejecutar pruebas de fuentes fuera de Windows.
    win32file = None

from connector_logging import get_logger
from fiscal_sequence import FiscalStore, PROTOCOL_VERSION, scope_from_config
import order_process
import order_return


LOGGER = get_logger("local_fiscal")
RESULT_POLL_INTERVAL_SECONDS = 0.05


def store_from_config(config, base_dir: Path):
    expected = scope_from_config(config)
    return FiscalStore(Path(base_dir) / "fiscal_state.sqlite3", expected)


def publish_once(path: Path, content: bytes):
    """Publica un TXT completo sin reemplazar uno existente (NTFS requerido)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".fiscal-", suffix=".pending", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def matching_files(folder: Path, pattern: str) -> list[Path]:
    """Busca con el filtro nativo de Windows sin enumerar toda la carpeta.

    ``Path.glob`` aplica el patrón en Python después de recorrer el directorio.
    Eso resulta costoso cuando Resultado_FF acumula decenas de miles de
    archivos. ``FindFilesW`` entrega el patrón al sistema de archivos.
    """
    directory = Path(folder)
    if not directory.is_dir():
        return []
    if win32file is not None:
        try:
            entries = win32file.FindFilesW(str(directory / pattern))
        except ImportError:
            # Defensa para un paquete incompleto: la venta sigue funcionando
            # con la búsqueda anterior, aunque sea más lenta.
            entries = None
        if entries is not None:
            return [
                directory / entry[8]
                for entry in entries
                if entry[8] not in (".", "..") and (directory / entry[8]).is_file()
            ]
    return [path for path in directory.glob(pattern) if path.is_file()]


def find_result(
    number: str,
    agent_dir: Path,
    result_dir: Path,
    timeout: float,
    poll_interval: float = RESULT_POLL_INTERVAL_SECONDS,
    search_archive: bool = True,
):
    """Busca una respuesta completa con un sondeo corto y acotado.

    El Agente FF crea el archivo de forma externa. Un intervalo de 50 ms evita
    sumar hasta 250 ms al ACK sin modificar el TXT, la numeración ni la espera
    máxima del protocolo.
    """
    deadline = time.monotonic() + timeout
    archive_pending = search_archive
    while True:
        candidates = []
        folders = [agent_dir]
        if archive_pending:
            folders.append(result_dir)
            archive_pending = False
        for folder in folders:
            candidates.extend(
                path for path in matching_files(folder, f"result_{number}_*")
                if order_return._identificador_de_respuesta(path.name) == number
            )
        candidates.sort(key=lambda path: int(path.stem.rsplit("_", 1)[-1]), reverse=True)
        for path in candidates:
            try:
                text = order_return._leer_texto(path, intentos=2, espera=0.1)
            except (OSError, ValueError):
                continue
            lines = [line.strip() for line in text.splitlines()]
            if len(lines) == 2 and all(lines):
                return {"procesado": False, "mensaje": lines[1]}
            if len(lines) == 3 and all(lines) and not lines[0].lower().startswith("error"):
                return {"procesado": True, "contenido": {"identificador": lines[0], "comprobante": lines[1], "fecha_completa": lines[2]}}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(poll_interval, remaining))


def log_cycle_timing(
    *,
    mode: str,
    number: str | None,
    outcome: str,
    started_at: float,
    prepare_seconds: float,
    lookup_seconds: float = 0.0,
    claim_seconds: float = 0.0,
    publish_seconds: float = 0.0,
    wait_seconds: float = 0.0,
    save_seconds: float = 0.0,
) -> None:
    LOGGER.info(
        "Tiempo fiscal | modo=%s | order=%s | resultado=%s | "
        "preparar_ms=%.1f | buscar_previa_ms=%.1f | reservar_envio_ms=%.1f | "
        "publicar_ms=%.1f | esperar_ff_ms=%.1f | "
        "guardar_ms=%.1f | total_ms=%.1f",
        mode,
        number or "SIN_DOCUMENTO",
        outcome,
        prepare_seconds * 1000,
        lookup_seconds * 1000,
        claim_seconds * 1000,
        publish_seconds * 1000,
        wait_seconds * 1000,
        save_seconds * 1000,
        (time.perf_counter() - started_at) * 1000,
    )


class LocalFiscalService:
    def __init__(self, store: FiscalStore, agent_dir: Path, result_dir: Path, timeout=15, renderer=None):
        self.store = store
        self.agent_dir = Path(agent_dir)
        self.result_dir = Path(result_dir)
        self.timeout = timeout
        self.renderer = renderer or self.render_document
        self.lock = threading.Lock()

    def render_document(self, payload, valid, refund):
        # La cabecera usa el mismo perfil ya validado, no globals de otra configuración.
        return order_process.render_document(payload, valid, refund, fiscal_scope=self.store.scope)

    def request_status(self, payload: dict) -> dict:
        """Consult a UID without changing the counter, SQLite state or FF queues."""
        if not isinstance(payload, dict):
            raise ValueError("La consulta fiscal debe ser un objeto")
        if type(payload.get("FISCAL_PROTOCOL")) is not int or payload["FISCAL_PROTOCOL"] != PROTOCOL_VERSION:
            raise ValueError("Protocolo fiscal local incompatible")
        identity = payload.get("FISCAL_IDENTITY")
        if not isinstance(identity, dict) or set(identity) != set(self.store.scope):
            raise ValueError("Falta la identidad fiscal completa para consultar el pedido")
        mismatches = [key for key, value in self.store.scope.items() if identity[key] != value]
        if mismatches:
            raise ValueError(
                "La consulta pertenece a otra compañía/emisor o TPV (" +
                ", ".join(mismatches) + ")"
            )
        status = self.store.order_status(payload.get("ORDER_UID"))
        return {
            **status,
            "protocol": PROTOCOL_VERSION,
            "fiscal_identity": dict(self.store.scope),
        }

    def process(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("La solicitud fiscal debe ser un objeto")
        with self.lock:
            started_at = time.perf_counter()
            prepare_started_at = time.perf_counter()
            record = self.store.prepare(payload, self.renderer)
            prepare_seconds = time.perf_counter() - prepare_started_at
            if record.get("amended_before_dispatch"):
                LOGGER.info(
                    "Pagos corregidos antes de publicar | order=%s | uid=%s | "
                    "mismo número conservado",
                    record["number"],
                    record["order_uid"],
                )
            lookup_seconds = 0.0
            claim_seconds = 0.0
            publish_seconds = 0.0
            wait_seconds = 0.0
            save_seconds = 0.0
            number = record["number"]
            identity = {"order_uid": record["order_uid"], "fiscal_number": number,
                        "protocol": PROTOCOL_VERSION, "fiscal_identity": dict(self.store.scope)}
            if record["response"]:
                log_cycle_timing(
                    mode="local",
                    number=number,
                    outcome="replayed",
                    started_at=started_at,
                    prepare_seconds=prepare_seconds,
                )
                return {**json.loads(record["response"]), **identity, "replayed": True}
            order_return._registrar_orden_activa(number)
            try:
                # Recupera también respuestas ya archivadas por el watcher.
                lookup_started_at = time.perf_counter()
                result = find_result(
                    number,
                    self.agent_dir,
                    self.result_dir,
                    0,
                    search_archive=record["state"] != "prepared",
                )
                lookup_seconds = time.perf_counter() - lookup_started_at
                claim_started_at = time.perf_counter()
                claimed = result is None and self.store.claim_dispatch(record["order_uid"])
                claim_seconds = time.perf_counter() - claim_started_at
                if claimed:
                    publish_started_at = time.perf_counter()
                    try:
                        publish_once(self.agent_dir / f"{number}.txt", record["document"])
                    except OSError as exc:
                        publish_seconds = time.perf_counter() - publish_started_at
                        log_cycle_timing(
                            mode="local",
                            number=number,
                            outcome="publication_pending",
                            started_at=started_at,
                            prepare_seconds=prepare_seconds,
                            lookup_seconds=lookup_seconds,
                            claim_seconds=claim_seconds,
                            publish_seconds=publish_seconds,
                        )
                        return {**identity, "procesado": False, "pending": True,
                                "mensaje": f"No se pudo publicar {number}. No se reintentará el envío automáticamente; concilie con FF. {exc}"}
                    publish_seconds = time.perf_counter() - publish_started_at
                if result is None:
                    wait_started_at = time.perf_counter()
                    result = find_result(
                        number,
                        self.agent_dir,
                        self.result_dir,
                        self.timeout,
                        search_archive=False,
                    )
                    wait_seconds = time.perf_counter() - wait_started_at
                if result is None:
                    log_cycle_timing(
                        mode="local",
                        number=number,
                        outcome="response_pending",
                        started_at=started_at,
                        prepare_seconds=prepare_seconds,
                        lookup_seconds=lookup_seconds,
                        claim_seconds=claim_seconds,
                        publish_seconds=publish_seconds,
                        wait_seconds=wait_seconds,
                    )
                    return {**identity, "procesado": False, "pending": True,
                            "mensaje": "Respuesta fiscal pendiente. Conserve esta venta y vuelva a consultar; no se generará otro número ni se reenviará el TXT."}
                save_started_at = time.perf_counter()
                self.store.save_response(record["order_uid"], result)
                save_seconds = time.perf_counter() - save_started_at
                log_cycle_timing(
                    mode="local",
                    number=number,
                    outcome="processed" if result.get("procesado") else "rejected",
                    started_at=started_at,
                    prepare_seconds=prepare_seconds,
                    lookup_seconds=lookup_seconds,
                    claim_seconds=claim_seconds,
                    publish_seconds=publish_seconds,
                    wait_seconds=wait_seconds,
                    save_seconds=save_seconds,
                )
                return {**result, **identity, "replayed": False}
            finally:
                order_return._liberar_orden_activa(number)
