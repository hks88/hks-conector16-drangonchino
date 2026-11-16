"""Selección explícita de protocolo. Nunca adivina ni cambia de modo por un error."""
import json
from pathlib import Path
import re

from local_fiscal import LocalFiscalService, store_from_config

LOCAL_MARKER = "fiscal_local_activated.json"
LOCAL_STATE = "fiscal_state.sqlite3"


def configured_mode(config):
    # Un config.ini intacto de 5.1.9 no contiene esta sección.
    if not config.has_section("FiscalSequence"):
        return "odoo"
    mode = config.get("FiscalSequence", "modo", fallback="").strip()
    if mode not in ("odoo", "local"):
        raise ValueError("FiscalSequence.modo debe ser odoo o local. No se elegirá automáticamente.")
    return mode


def require_legacy_allowed(base_dir):
    root = Path(base_dir)
    for name in (LOCAL_STATE, LOCAL_STATE + "-wal", LOCAL_STATE + "-shm", LOCAL_MARKER):
        if (root / name).exists():
            raise ValueError("Esta PC tiene estado o activación fiscal local. No se permite volver a modo=odoo. Conserve el SQLite y restaure la configuración local.")


def preserve_activation(base_dir, identity):
    path = Path(base_dir) / LOCAL_MARKER
    expected = {"protocol": 2, "identity": identity}
    try:
        # Fallo o interrupción deja una marca que bloquea el retorno al modo viejo.
        with path.open("x", encoding="utf-8") as stream:
            json.dump(expected, stream, sort_keys=True)
            stream.flush()
            import os
            os.fsync(stream.fileno())
    except FileExistsError:
        if json.loads(path.read_text(encoding="utf-8")) != expected:
            raise ValueError("La marca de activación pertenece a otro perfil. No la borre; revise el respaldo y la configuración.")


class FiscalRuntime:
    def __init__(self, config, base_dir, agent_dir, result_dir, timeout=15):
        self.base_dir = Path(base_dir)
        self.mode = configured_mode(config)
        self.identity = None
        if self.mode == "local":
            store = store_from_config(config, self.base_dir)
            store.status()  # Verifica SQLite y perfil ANTES de abrir el puerto de salud.
            preserve_activation(self.base_dir, store.scope)
            self.identity = dict(store.scope)
            self.service = LocalFiscalService(store, agent_dir, result_dir, timeout)
        else:
            require_legacy_allowed(self.base_dir)
            branch = config.get("Target", "sucursal", fallback="").strip()
            point = config.get("Target", "punto_de_facturacion", fallback="").strip()
            if not re.fullmatch(r"[A-Z0-9]{4}", branch) or not re.fullmatch(r"[0-9]{3}", point) or point == "000":
                raise ValueError("Configure sucursal de 4 caracteres en mayúscula (A-Z o 0-9) y punto de 001 a 999 antes de iniciar.")
            from legacy_fiscal import LegacyFiscalService
            self.service = LegacyFiscalService(self.base_dir, agent_dir, result_dir,
                                               {"sucursal": branch, "punto": point}, timeout)

    def require_mode(self, mode):
        if mode != self.mode:
            raise ValueError("El protocolo del TPV no coincide con modo=%s de esta PC. Coordine la activación; no se cambiará la numeración automáticamente." % self.mode)
        if mode == "odoo":
            require_legacy_allowed(self.base_dir)

    def capabilities(self):
        self.require_mode(self.mode)
        if self.mode == "local":
            self.service.store.status()
        return {"version": "5.2.8", "mode": self.mode, "ready": True,
                "protocols": [2] if self.mode == "local" else [0],
                "identity": self.identity}
