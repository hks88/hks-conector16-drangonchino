from __future__ import annotations

import asyncio
import configparser
import os
import sys
import threading
import time
from pathlib import Path
from tkinter import Label, Tk, messagebox
from typing import Callable

from connector_logging import (
    active_log_path,
    configure_logging,
    get_logger,
    install_exception_hooks,
    log_asyncio_exception,
)

VERSION_LOCAL = "5.1.9"
configure_logging()
install_exception_hooks()
LOGGER = get_logger("frontend")
SOCKETIO_LOGGER = get_logger("socketio")
ENGINEIO_LOGGER = get_logger("engineio")
# Engine.IO registra el contenido completo de cada paquete en INFO. Se conserva
# WARNING/ERROR para diagnosticar transporte sin copiar datos fiscales al log.
ENGINEIO_LOGGER.setLevel("WARNING")

from hypercorn.asyncio import serve
from hypercorn.config import Config
import serial
import socketio
import win32print
from PIL import Image, ImageTk

import order_process
import order_return


def application_dir() -> Path:
    """Devuelve la carpeta externa del script o del ejecutable PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def read_config(path: Path) -> configparser.ConfigParser:
    """Lee config.ini aceptando UTF-8 con BOM y archivos antiguos CP1252."""
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            parser.read(path, encoding="cp1252")
            LOGGER.warning("config.ini antiguo leído con codificación CP1252")
        except Exception:
            LOGGER.exception("No fue posible leer config.ini en CP1252: %s", path)
            raise
    except Exception:
        LOGGER.exception("No fue posible leer config.ini: %s", path)
        raise
    return parser


BASE_DIR = application_dir()
config = read_config(BASE_DIR / "config.ini")
gaveta_port = config.get("Target", "gaveta", fallback="").strip()
printer_name = config.get("Target", "Nombre_impresora", fallback="").strip()
branch = config.get("Target", "sucursal", fallback="").strip()
POS = config.get("Target", "punto_de_facturacion", fallback="").strip()

DRAWER_MODE_CASH = "efectivo"
DRAWER_MODE_ALWAYS = "siempre"
DRAWER_MODE_ALIASES = {
    "efectivo": DRAWER_MODE_CASH,
    "cash": DRAWER_MODE_CASH,
    "siempre": DRAWER_MODE_ALWAYS,
    "always": DRAWER_MODE_ALWAYS,
}
CASH_PAYMENT_NAMES = {"cash", "efectivo"}


def normalize_drawer_mode(value: object) -> str:
    """Normaliza el modo configurado y usa efectivo como opción segura."""
    normalized = str(value or "").strip().casefold()
    return DRAWER_MODE_ALIASES.get(normalized, DRAWER_MODE_CASH)


def should_open_drawer(mode: object, payment_lines: object) -> bool:
    """Decide si la orden requiere apertura automática de la gaveta."""
    normalized_mode = normalize_drawer_mode(mode)
    if normalized_mode == DRAWER_MODE_ALWAYS:
        return True

    for payment in payment_lines or []:
        if not isinstance(payment, dict):
            continue
        payment_name = str(payment.get("PAY_NAME", "")).strip().casefold()
        if payment_name in CASH_PAYMENT_NAMES:
            return True
    return False


raw_drawer_mode = config.get(
    "Target",
    "modo_apertura_gaveta",
    fallback=DRAWER_MODE_CASH,
)
drawer_mode = normalize_drawer_mode(raw_drawer_mode)
if str(raw_drawer_mode).strip().casefold() not in DRAWER_MODE_ALIASES:
    LOGGER.warning(
        "modo_apertura_gaveta no reconocido; se usará '%s'",
        DRAWER_MODE_CASH,
    )

# Texto original de la interfaz. No se toma desde config.ini para evitar cambios
# accidentales o problemas de codificación como "Factura FÃ¡cil".
WINDOW_TITLE = "HKS"
CLIENT_NAME = "Hong Kong Smart"

# threading.Event es seguro para comunicar el cierre desde el hilo de Tkinter
# hacia el loop asyncio donde está Hypercorn.
shutdown_requested = threading.Event()


def parse_new_order_event(event_args: tuple) -> tuple[dict, object, object, object, object]:
    """Valida la cantidad y el tipo de argumentos enviados por Odoo."""
    args = event_args
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        args = tuple(args[0])

    if not 3 <= len(args) <= 5:
        argument_types = [type(value).__name__ for value in args]
        raise ValueError(
            "Evento new_order incompatible: se esperaban entre 3 y 5 "
            f"argumentos y se recibieron {len(args)}; tipos={argument_types}"
        )

    order_data, valid, is_refund, *optional = args
    if not isinstance(order_data, dict):
        raise TypeError(
            "Evento new_order incompatible: el primer argumento debe ser un "
            f"objeto y se recibió {type(order_data).__name__}"
        )

    pos = optional[0] if len(optional) >= 1 else False
    devol = optional[1] if len(optional) >= 2 else False
    return order_data, valid, is_refund, pos, devol


def request_shutdown() -> None:
    """Solicita el cierre coordinado del servidor y de la interfaz."""
    shutdown_requested.set()


async def wait_for_shutdown() -> None:
    """Adaptador asíncrono usado como shutdown_trigger de Hypercorn."""
    await asyncio.to_thread(shutdown_requested.wait)


def force_exit_after_timeout(timeout: float = 5.0) -> None:
    """Garantiza que no quede el proceso PyInstaller residente al cerrar."""
    time.sleep(timeout)
    os._exit(0)


class Server:
    def __init__(self) -> None:
        self.sio = socketio.AsyncServer(
            cors_allowed_origins="*",
            async_mode="asgi",
            logger=SOCKETIO_LOGGER,
            engineio_logger=ENGINEIO_LOGGER,
        )
        self.ui: Frontend | None = None
        self.drawer_lock = threading.Lock()
        self.frontend_thread = threading.Thread(
            target=self.start_frontend,
            name="ConnectorUI",
            daemon=True,
        )
        self.frontend_thread.start()

        @self.sio.event
        async def connect(sid, environ, auth=None):
            LOGGER.info(
                "ODOO conectado | sid=%s | origen=%s | remoto=%s | agente=%s | auth=%s",
                sid,
                environ.get("HTTP_ORIGIN", "N/D"),
                environ.get("REMOTE_ADDR", "N/D"),
                environ.get("HTTP_USER_AGENT", "N/D"),
                "sí" if auth else "no",
            )

        @self.sio.event
        async def new_order(sid, *event_args):
            return await self.process_new_order_event(sid, event_args)

        @self.sio.event
        async def disconnect(sid, reason=None):
            LOGGER.info("ODOO desconectado | sid=%s | motivo=%s", sid, reason or "N/D")

        watcher_thread = threading.Thread(
            target=order_return.vigilar_resultados_pendientes,
            name="ResultWatcher",
            daemon=True,
        )
        watcher_thread.start()
        LOGGER.info("Watcher de resultados iniciado")

    async def emit_processing_error(
        self,
        sid: str,
        error: Exception,
        order_number: object = None,
    ) -> dict:
        response = {
            "success": False,
            "error": str(error),
            "order": order_number,
        }
        try:
            await self.sio.emit("fel_error_event", response, to=sid)
        except Exception:
            LOGGER.exception(
                "No fue posible devolver fel_error_event a Odoo | sid=%s | order=%s",
                sid,
                order_number or "N/D",
            )
        return response

    async def process_new_order_event(self, sid: str, event_args: tuple) -> dict | None:
        """Procesa una orden y registra cualquier error de integración o documento."""
        start = time.perf_counter()
        order_number: object = None

        try:
            order_data, valid, is_refund, pos, devol = parse_new_order_event(event_args)
            order_number = order_data.get("ORDER_NUMBER")
            order_lines = order_data.get("ORDERLINES")
            payment_lines = order_data.get("PAYMENTLINES")
            order_line_fields = sorted(
                {
                    str(field)
                    for line in (order_lines or [])
                    if isinstance(line, dict)
                    for field in line.keys()
                }
            )
            LOGGER.info(
                "ODOO new_order recibido | sid=%s | order=%s | valid=%r | "
                "refund=%r | pos=%r | devol=%r | líneas=%s | pagos=%s | "
                "campos=%s | campos_linea=%s",
                sid,
                order_number or "N/D",
                valid,
                is_refund,
                pos,
                devol,
                len(order_lines) if isinstance(order_lines, list) else "tipo inválido",
                len(payment_lines) if isinstance(payment_lines, list) else "tipo inválido",
                sorted(order_data.keys()),
                order_line_fields,
            )

            if not valid:
                LOGGER.warning(
                    "Orden rechazada antes de generar TXT porque Odoo envió valid=False "
                    "| order=%s",
                    order_number or "N/D",
                )
                return {"success": False, "error": "Orden no válida"}

            output_path = order_process.order(order_data, valid, is_refund)
            if output_path:
                txt_path = Path(output_path)
                LOGGER.info(
                    "TXT generado | order=%s | ruta=%s | bytes=%s",
                    order_number or "N/D",
                    txt_path,
                    txt_path.stat().st_size if txt_path.is_file() else "no encontrado",
                )
            else:
                LOGGER.info(
                    "La orden no requiere TXT fiscal | order=%s",
                    order_number or "N/D",
                )

            if should_open_drawer(drawer_mode, payment_lines or []):
                await asyncio.to_thread(self.open_drawer, "automática")

            if pos:
                if devol:
                    data = await asyncio.to_thread(order_return.observar, order_data)
                    await self.sio.emit("fel_response_event", data, to=sid)
                    LOGGER.info(
                        "Respuesta FEL enviada a Odoo | order=%s | resultado=%r | segundos=%.3f",
                        order_number or "N/D",
                        data,
                        time.perf_counter() - start,
                    )
                    return data

                LOGGER.info(
                    "Odoo no solicitó devolución de datos | order=%s",
                    order_number or "N/D",
                )
                product_name = (order_lines or [{}])[0].get("PRODUCT_NAME", "")
                if product_name != "Abono":
                    # La espera del resultado es bloqueante; se mueve fuera del loop
                    # asyncio para no congelar nuevas conexiones Socket.IO.
                    await asyncio.to_thread(order_return.ordenar_sin_devolver, order_data)
                LOGGER.info(
                    "Orden finalizada sin respuesta a Odoo | order=%s | segundos=%.3f",
                    order_number or "N/D",
                    time.perf_counter() - start,
                )
                return {
                    "success": True,
                    "file": str(output_path) if output_path else None,
                }

            data = await asyncio.to_thread(order_return.observar, order_data)
            LOGGER.info(
                "Orden finalizada | order=%s | resultado=%r | segundos=%.3f",
                order_number or "N/D",
                data,
                time.perf_counter() - start,
            )
            return data
        except Exception as exc:
            LOGGER.exception(
                "Error procesando documento recibido desde Odoo | sid=%s | "
                "order=%s | argumentos=%s",
                sid,
                order_number or "N/D",
                len(event_args),
            )
            return await self.emit_processing_error(sid, exc, order_number)

    def open_drawer(self, source: str = "manual") -> bool:
        """Abre la gaveta una sola vez mediante impresora o puerto serial."""
        if not self.drawer_lock.acquire(blocking=False):
            LOGGER.info("Ya existe una apertura de gaveta en curso")
            return False

        try:
            if printer_name:
                printer_handle = None
                try:
                    LOGGER.info(
                        "Apertura de gaveta por impresora | origen=%s | impresora=%s",
                        source,
                        printer_name,
                    )
                    printer_handle = win32print.OpenPrinter(printer_name)
                    win32print.StartDocPrinter(
                        printer_handle,
                        1,
                        ("OpenCashDrawer", None, "RAW"),
                    )
                    win32print.StartPagePrinter(printer_handle)
                    win32print.WritePrinter(
                        printer_handle,
                        b"\x1B\x70\x00\x19\xFA",
                    )
                    win32print.EndPagePrinter(printer_handle)
                    win32print.EndDocPrinter(printer_handle)
                    LOGGER.info("Gaveta abierta desde impresora USB")
                    return True
                except Exception as exc:
                    LOGGER.exception(
                        "Fallo al abrir gaveta con impresora | impresora=%s",
                        printer_name,
                    )
                finally:
                    if printer_handle is not None:
                        try:
                            win32print.ClosePrinter(printer_handle)
                        except Exception:
                            LOGGER.exception("Fallo al cerrar el handle de la impresora")

            if gaveta_port:
                try:
                    LOGGER.info(
                        "Apertura de gaveta por serial | origen=%s | puerto=%s",
                        source,
                        gaveta_port,
                    )
                    with serial.Serial(
                        gaveta_port,
                        baudrate=9600,
                        timeout=1,
                    ) as serial_port:
                        serial_port.write(b"a")
                    LOGGER.info("Gaveta abierta desde puerto serial")
                    return True
                except Exception:
                    LOGGER.exception(
                        "Error al abrir gaveta por serial | puerto=%s",
                        gaveta_port,
                    )

            if not printer_name and not gaveta_port:
                LOGGER.info("Gaveta no configurada; se omite su apertura")
            return False
        finally:
            self.drawer_lock.release()

    def open_drawer_manually(self) -> None:
        """Ejecuta la apertura manual fuera del hilo de Tkinter."""
        threading.Thread(
            target=self.open_drawer,
            args=("manual por doble clic",),
            name="ManualDrawerOpen",
            daemon=True,
        ).start()

    def start_frontend(self) -> None:
        self.ui = Frontend(drawer_callback=self.open_drawer_manually)
        self.ui.mainloop()

    async def run_hypercorn(self) -> None:
        app = socketio.ASGIApp(self.sio)
        server_config = Config()
        server_config.bind = ["localhost:3222"]
        LOGGER.info("Servidor Socket.IO iniciando en http://localhost:3222")
        await serve(
            app,
            server_config,
            shutdown_trigger=wait_for_shutdown,
        )


class Frontend(Tk):
    def __init__(
        self,
        drawer_callback: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._closing = False
        self.drawer_callback = drawer_callback
        self.title(WINDOW_TITLE)
        self.configure(bg="#FFFFFF")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_exit)
        self.state(newstate="iconic")

        script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.directory = getattr(sys, "_MEIPASS", script_dir)

        try:
            icon_path = os.path.join(self.directory, "conector.ico")
            icon_image = Image.open(icon_path)
            self.icon_image = ImageTk.PhotoImage(icon_image)
            self.wm_iconphoto(True, self.icon_image)
        except Exception:
            LOGGER.exception("No se pudo cargar el ícono de la ventana")

        self.area_gui()

    def area_gui(self) -> None:
        font_title = ("Segoe UI", 16, "bold")
        font_normal = ("Segoe UI", 10, "bold")

        Label(
            text=CLIENT_NAME,
            fg="black",
            font=font_title,
            bg="#FFFFFF",
        ).pack(pady=(10, 0))

        Label(
            text=f"Versión {VERSION_LOCAL}",
            fg="gray",
            font=font_normal,
            bg="#FFFFFF",
        ).pack(pady=(0, 10))

        try:
            logo_path = os.path.join(self.directory, "favicon.png")
            resized_image = Image.open(logo_path).resize((100, 100))
            self.logo = ImageTk.PhotoImage(resized_image)
            self.logo_label = Label(
                image=self.logo,
                bg="#FFFFFF",
                cursor="hand2",
            )
            self.logo_label.pack(pady=(0, 10))
            self.logo_label.bind(
                "<Double-Button-1>",
                self.on_logo_double_click,
            )
        except Exception:
            LOGGER.exception("No se pudo cargar el logo de la ventana")

        if printer_name:
            drawer_target = f"Impresora: {printer_name}"
        elif gaveta_port:
            drawer_target = f"Serial: {gaveta_port}"
        else:
            drawer_target = "N/D"

        Label(
            text=f"Gaveta: {drawer_target} | Modo: {drawer_mode}",
            fg="black",
            font=font_normal,
            bg="#FFFFFF",
        ).pack()

        Label(
            text=f"Sucursal {branch or 'N/D'} | Punto {POS or 'N/D'}",
            fg="black",
            font=font_normal,
            bg="#FFFFFF",
        ).pack()

    def on_logo_double_click(self, _event=None) -> None:
        """Solicita una apertura manual al hacer doble clic en el logo."""
        if self.drawer_callback is None:
            LOGGER.info("No existe callback configurado para la gaveta")
            return
        self.drawer_callback()

    def on_exit(self) -> None:
        if self._closing:
            return

        close = messagebox.askyesno(
            message=(
                "SI CIERRA ESTA VENTANA NO PODRÁ FACTURAR\n\n"
                "¿ESTÁ SEGURO DE CERRAR LA APLICACIÓN?\n\n"
                "如果您关闭此窗口，您将无法开发票。您确定要关闭该应用程序吗?"
            ),
            title="CERRAR EL CONECTOR DE FACTURACIÓN ELECTRÓNICA",
            icon="error",
        )
        if not close:
            return

        self._closing = True
        LOGGER.info("Cierre solicitado desde la interfaz")

        # Respaldo final: si alguna tarea externa se queda bloqueada, termina el
        # proceso completo después de cinco segundos. El hilo es daemon para no
        # crear un nuevo proceso residente.
        threading.Thread(
            target=force_exit_after_timeout,
            name="ForcedShutdownGuard",
            daemon=True,
        ).start()

        request_shutdown()
        self.quit()
        self.destroy()


async def run_app() -> None:
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(log_asyncio_exception)
    LOGGER.info("=" * 72)
    LOGGER.info(
        "Inicio del Conector Factura Fácil | versión=%s | base=%s | log=%s",
        VERSION_LOCAL,
        BASE_DIR,
        active_log_path(),
    )
    LOGGER.info(
        "Configuración detectada | archivo=%s | sucursal=%s | punto=%s | "
        "impresora=%s | gaveta_serial=%s | modo_gaveta=%s",
        BASE_DIR / "config.ini",
        branch or "NO CONFIGURADA",
        POS or "NO CONFIGURADO",
        printer_name or "NO CONFIGURADA",
        gaveta_port or "NO CONFIGURADA",
        drawer_mode,
    )
    if not (BASE_DIR / "config.ini").is_file():
        LOGGER.error("No existe config.ini junto al conector")
    if not branch or not POS:
        LOGGER.warning(
            "Configuración fiscal incompleta: sucursal=%s, punto_de_facturacion=%s",
            branch or "vacío",
            POS or "vacío",
        )

    server = Server()
    try:
        await server.run_hypercorn()
    finally:
        LOGGER.info("Servidor detenido")
        request_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        LOGGER.info("Cierre solicitado por teclado")
        request_shutdown()
    except Exception:
        LOGGER.exception("Error fatal durante la ejecución del conector")
        raise
