from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from connector_logging import get_logger


LOGGER = get_logger("order_return")

def application_dir() -> Path:
    """Devuelve la carpeta externa del script o del ejecutable PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = application_dir()
AGENT_DIR = BASE_DIR / "Agente_FF"
RESULT_DIR = AGENT_DIR / "Resultado_FF"
WATCHER_MIN_AGE_SECONDS = 60.0

_ACTIVE_ORDERS: dict[str, int] = {}
_ACTIVE_ORDERS_LOCK = threading.Lock()


def ensure_directories() -> None:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)


def _registrar_orden_activa(identificador: str) -> None:
    """Impide que el watcher archive una respuesta que una orden está esperando."""
    with _ACTIVE_ORDERS_LOCK:
        _ACTIVE_ORDERS[identificador] = _ACTIVE_ORDERS.get(identificador, 0) + 1


def _liberar_orden_activa(identificador: str) -> None:
    with _ACTIVE_ORDERS_LOCK:
        referencias = _ACTIVE_ORDERS.get(identificador, 0)
        if referencias <= 1:
            _ACTIVE_ORDERS.pop(identificador, None)
        else:
            _ACTIVE_ORDERS[identificador] = referencias - 1


def _identificador_de_respuesta(nombre_archivo: str) -> str | None:
    """Extrae el identificador de result_<identificador>_<secuencia>."""
    nombre = Path(nombre_archivo).stem
    if not nombre.startswith("result_"):
        return None

    identificador, separador, secuencia = nombre[len("result_"):].rpartition("_")
    if not separador or not identificador or not secuencia.isdigit():
        return None
    return identificador


def _respuesta_de_orden_activa(nombre_archivo: str) -> bool:
    identificador = _identificador_de_respuesta(nombre_archivo)
    if identificador is None:
        return False
    with _ACTIVE_ORDERS_LOCK:
        return identificador in _ACTIVE_ORDERS


def escribir_log(mensaje: str) -> None:
    """Compatibilidad con llamadas antiguas al registro central."""
    normalized = mensaje.lstrip().upper()
    if normalized.startswith("[ERROR]"):
        LOGGER.error(mensaje)
    elif normalized.startswith(("[TIMEOUT]", "[WARN]")):
        LOGGER.warning(mensaje)
    else:
        LOGGER.info(mensaje)


def _leer_texto(
    ruta_archivo: str | Path,
    intentos: int = 6,
    espera: float = 0.3,
) -> str:
    """Lee una respuesta aunque el Agente aún la escriba o use ANSI.

    El Agente FF puede crear primero el archivo vacío, mantenerlo bloqueado
    durante unos milisegundos o escribir mensajes en CP1252. Se reintentan los
    errores transitorios y se aceptan UTF-8, CP1252 y Latin-1 para no perder el
    motivo real de un rechazo por un ``UnicodeDecodeError``.
    """
    ruta = Path(ruta_archivo)
    ultimo_error: Exception | None = None

    for intento in range(intentos):
        try:
            datos = ruta.read_bytes()
            if datos:
                texto = None
                codec_usado = ""
                for codec in ("utf-8-sig", "cp1252", "latin-1"):
                    try:
                        texto = datos.decode(codec)
                        codec_usado = codec
                        break
                    except UnicodeDecodeError:
                        continue

                if texto is None:
                    texto = datos.decode("cp1252", errors="replace")
                    codec_usado = "cp1252-replace"

                if intento or codec_usado != "utf-8-sig":
                    LOGGER.info(
                        "Respuesta del Agente FF leída | archivo=%s | "
                        "reintentos=%s | codificación=%s",
                        ruta.name,
                        intento,
                        codec_usado,
                    )
                return texto

            ultimo_error = ValueError("El archivo de respuesta está vacío")
        except OSError as exc:
            # Incluye PermissionError cuando el Agente conserva el archivo
            # abierto en exclusiva mientras termina de escribirlo.
            ultimo_error = exc

        if intento < intentos - 1:
            time.sleep(espera)

    if ultimo_error is not None:
        raise ultimo_error
    return ""


def leer_archivo(ruta_archivo: str | Path) -> dict:
    """Lee un result_*.txt del Agente FF y devuelve una estructura simple."""
    resultado: dict = {}
    try:
        lineas = [linea.strip() for linea in _leer_texto(ruta_archivo).splitlines()]

        if len(lineas) == 2 or (lineas and "error" in lineas[0].lower()):
            resultado.update({
                "procesado": False,
                "mensaje": lineas[1] if len(lineas) > 1 else "Archivo de error incompleto",
            })
        elif len(lineas) == 3:
            resultado.update({
                "procesado": True,
                "contenido": {
                    "identificador": lineas[0],
                    "comprobante": lineas[1],
                    "fecha_completa": lineas[2],
                },
            })
        else:
            resultado.update({
                "procesado": False,
                "mensaje": "Formato de archivo no válido",
            })
        if resultado.get("procesado"):
            LOGGER.info(
                "Respuesta del Agente FF procesada | archivo=%s | comprobante=%s",
                Path(ruta_archivo).name,
                resultado.get("contenido", {}).get("comprobante", "N/D"),
            )
        else:
            LOGGER.warning(
                "Respuesta de error del Agente FF | archivo=%s | mensaje=%s",
                Path(ruta_archivo).name,
                resultado.get("mensaje", "N/D"),
            )
    except Exception:
        LOGGER.exception("Error al leer respuesta del Agente FF: %s", ruta_archivo)
        resultado = {"procesado": False, "mensaje": "Error al leer el archivo"}
    return resultado


def esperar_resultado(
    identificador: str,
    src_path: str | Path | None = None,
    timeout: int = 15,
) -> str | None:
    """Espera un archivo result_<identificador>_n.txt durante un tiempo limitado."""
    src = Path(src_path) if src_path is not None else AGENT_DIR
    src.mkdir(parents=True, exist_ok=True)
    comodin = f"result_{identificador}_"
    tiempo_inicio = time.time()
    LOGGER.info(
        "Esperando respuesta del Agente FF | order=%s | carpeta=%s | timeout=%ss",
        identificador,
        src,
        timeout,
    )

    while time.time() - tiempo_inicio < timeout:
        archivos = [
            path for path in src.iterdir()
            if path.is_file() and path.name.startswith(comodin)
        ]
        if archivos:
            def sequence(path: Path) -> int:
                try:
                    return int(path.stem.split("_")[-1])
                except ValueError:
                    return -1

            archivos.sort(key=sequence)
            LOGGER.info(
                "Respuesta del Agente FF detectada | order=%s | archivo=%s",
                identificador,
                archivos[-1].name,
            )
            return archivos[-1].name
        time.sleep(0.5)

    LOGGER.warning(
        "Tiempo agotado esperando result_%s_n después de %ss",
        identificador,
        timeout,
    )
    return None


def mover_archivo(
    nombre_archivo: str,
    src_path: str | Path | None = None,
    dst_path: str | Path | None = None,
) -> Path | None:
    """Mueve un resultado a Resultado_FF y evita sobrescribir archivos existentes."""
    src = Path(src_path) if src_path is not None else AGENT_DIR
    dst = Path(dst_path) if dst_path is not None else RESULT_DIR
    dst.mkdir(parents=True, exist_ok=True)

    ruta_origen = src / nombre_archivo
    ruta_destino = dst / nombre_archivo

    if ruta_destino.is_file():
        stem_parts = ruta_destino.stem.split("_")
        try:
            stem_parts[-1] = str(int(stem_parts[-1]) + 1)
            nuevo_nombre = "_".join(stem_parts) + ruta_destino.suffix
        except (ValueError, IndexError):
            nuevo_nombre = f"{ruta_destino.stem}_{int(time.time())}{ruta_destino.suffix}"
        ruta_destino = dst / nuevo_nombre
        LOGGER.warning(
            "Conflicto de nombre al archivar respuesta | original=%s | nuevo=%s",
            nombre_archivo,
            ruta_destino.name,
        )

    try:
        os.replace(ruta_origen, ruta_destino)
        LOGGER.info(
            "Respuesta archivada | original=%s | destino=%s",
            nombre_archivo,
            ruta_destino,
        )
    except Exception:
        LOGGER.exception(
            "Falló el archivado de la respuesta | archivo=%s | destino=%s",
            nombre_archivo,
            ruta_destino,
        )
        return None

    return ruta_destino


def observar(order_data: dict) -> dict | None:
    """Espera, lee y archiva el resultado correspondiente a una orden."""
    ensure_directories()
    identificador = order_data.get("ORDER_NUMBER")
    if not identificador:
        LOGGER.error("No se puede observar respuesta: ORDER_NUMBER no proporcionado")
        return None

    identificador = str(identificador)
    _registrar_orden_activa(identificador)
    try:
        nombre_archivo = esperar_resultado(identificador)
        if not nombre_archivo:
            return {"procesado": False, "mensaje": "Tiempo de espera agotado"}

        time.sleep(1)
        ruta_archivo = AGENT_DIR / nombre_archivo
        resultado = leer_archivo(ruta_archivo)
        mover_archivo(nombre_archivo)
        return resultado
    finally:
        _liberar_orden_activa(identificador)


def ordenar_sin_devolver(order_data: dict) -> None:
    """Espera el resultado correspondiente y lo archiva sin devolverlo a Odoo."""
    ensure_directories()
    identificador = order_data.get("ORDER_NUMBER")
    if not identificador:
        LOGGER.error("No se puede archivar respuesta: ORDER_NUMBER no proporcionado")
        return

    identificador = str(identificador)
    _registrar_orden_activa(identificador)
    try:
        nombre_archivo = esperar_resultado(identificador)
        if not nombre_archivo:
            return

        time.sleep(1)
        mover_archivo(nombre_archivo)
    finally:
        _liberar_orden_activa(identificador)


def archivar_resultados_pendientes(
    antiguedad_minima: float = WATCHER_MIN_AGE_SECONDS,
) -> int:
    """Archiva una vez los resultados realmente huérfanos y devuelve el total."""
    ensure_directories()
    ahora = time.time()
    archivados = 0

    archivos = [
        path for path in AGENT_DIR.iterdir()
        if path.is_file() and path.name.startswith("result_")
    ]
    for ruta_archivo in archivos:
        if _respuesta_de_orden_activa(ruta_archivo.name):
            LOGGER.debug(
                "Watcher omitió respuesta de una orden activa | archivo=%s",
                ruta_archivo.name,
            )
            continue

        try:
            antiguedad = ahora - ruta_archivo.stat().st_mtime
        except FileNotFoundError:
            continue
        if antiguedad < antiguedad_minima:
            continue

        destino = mover_archivo(ruta_archivo.name)
        if destino is not None:
            archivados += 1
            LOGGER.info(
                "Watcher archivó una respuesta pendiente | archivo=%s",
                ruta_archivo.name,
            )

    return archivados


def vigilar_resultados_pendientes(intervalo: int = 30) -> None:
    """Mueve periódicamente resultados huérfanos a Resultado_FF."""
    ensure_directories()
    LOGGER.info(
        "Watcher de respuestas pendientes activo | intervalo=%ss | antigüedad_mínima=%ss",
        intervalo,
        WATCHER_MIN_AGE_SECONDS,
    )

    while True:
        try:
            archivar_resultados_pendientes()
        except Exception:
            LOGGER.exception("Error general en el watcher de respuestas")

        time.sleep(intervalo)
