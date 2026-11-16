from __future__ import annotations

import shutil
import sys
import hashlib
import re
import zipfile
from pathlib import Path


def fail(message: str, exit_code: int = 1) -> None:
    print(f"\n[ERROR] {message}\n")
    raise SystemExit(exit_code)


def create_version_resource(path: Path, version: str) -> None:
    parts = [int(part) for part in version.split(".")]
    parts.extend([0] * (4 - len(parts)))
    version_tuple = tuple(parts[:4])
    dotted_version = ".".join(str(part) for part in version_tuple)
    path.write_text(
        """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'HKSPOS'),
         StringStruct('FileDescription', 'Conector de Facturacion Electronica HKSPOS'),
         StringStruct('FileVersion', '{dotted_version}'),
         StringStruct('InternalName', 'Conector_V'),
         StringStruct('LegalCopyright', 'HKSPOS'),
         StringStruct('OriginalFilename', 'Conector_V{version}.exe'),
         StringStruct('ProductName', 'Conector HKSPOS'),
         StringStruct('ProductVersion', '{dotted_version}')]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""".format(
            version_tuple=version_tuple,
            dotted_version=dotted_version,
            version=version,
        ),
        encoding="utf-8",
    )


def frontend_version(frontend: Path) -> str:
    """Obtiene la versión que la interfaz y log.txt muestran al cliente."""
    frontend_source = frontend.read_text(encoding="utf-8")
    match = re.search(
        r'^VERSION_LOCAL\s*=\s*["\']([^"\']+)["\']',
        frontend_source,
        re.MULTILINE,
    )
    if not match:
        raise ValueError("No se encontró VERSION_LOCAL en frontend.py.")
    return match.group(1)


def validate_version_consistency(version: str, frontend: Path) -> str:
    """Impide generar un EXE cuyo nombre y log indiquen versiones distintas."""
    visible_version = frontend_version(frontend)
    if visible_version != version:
        raise ValueError(
            f"VERSION indica '{version}', pero frontend.py declara "
            f"VERSION_LOCAL = '{visible_version}'. Ambas versiones deben coincidir."
        )
    return visible_version


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    frontend = base_dir / "frontend.py"
    logging_module = base_dir / "connector_logging.py"
    favicon = base_dir / "favicon.png"
    icon = base_dir / "conector.ico"
    config = base_dir / "config.ini"
    version_source = base_dir / "VERSION"
    dist_dir = base_dir / "dist"
    build_dir = base_dir / "build"
    version_resource = base_dir / ".version_info.generated.txt"

    required_files = (frontend, logging_module, favicon, icon, config, version_source)
    missing = [str(path.name) for path in required_files if not path.is_file()]
    if missing:
        fail("Faltan archivos requeridos: " + ", ".join(missing))

    version = version_source.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
        fail("VERSION no contiene una version valida, por ejemplo 5.1.4.")

    try:
        validate_version_consistency(version, frontend)
    except ValueError as exc:
        fail(str(exc))
    print(f"[OK] Versión coherente: VERSION = VERSION_LOCAL = {version}")

    try:
        import PyInstaller.__main__
    except ImportError:
        fail(
            "PyInstaller no está instalado en este Python.\n"
            "Ejecute:\n"
            "  py -m pip install --upgrade pip setuptools wheel\n"
            "  py -m pip install pyinstaller pillow pyserial "
            "python-socketio hypercorn pywin32"
        )

    # Elimina únicamente salidas anteriores de compilación.
    if build_dir.exists():
        if build_dir.resolve().parent != base_dir.resolve() or build_dir.is_symlink():
            fail("Ruta de build inesperada; no se eliminará.")
        shutil.rmtree(build_dir)

    generic_executable = dist_dir / "Conector_V.exe"
    versioned_executable = dist_dir / f"Conector_V{version}.exe"
    for old_exe in (generic_executable, versioned_executable):
        if old_exe.exists():
            old_exe.unlink()

    create_version_resource(version_resource, version)

    add_data_separator = ";" if sys.platform.startswith("win") else ":"

    args = [
        str(frontend),
        "--name", "Conector_V",
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--noupx",
        "--icon", str(icon),
        "--version-file", str(version_resource),
        "--add-data", f"{favicon}{add_data_separator}.",
        "--add-data", f"{icon}{add_data_separator}.",
        "--hidden-import", "engineio.async_drivers.asgi",
        "--hidden-import", "hypercorn.asyncio",
        "--hidden-import", "win32timezone",
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir),
        "--specpath", str(base_dir),
    ]

    print("[INFO] Compilando Conector_V...")
    try:
        PyInstaller.__main__.run(args)
    finally:
        version_resource.unlink(missing_ok=True)

    executable = generic_executable if sys.platform.startswith("win") else dist_dir / "Conector_V"
    if not executable.is_file():
        fail(
            "PyInstaller terminó, pero no se encontró el ejecutable esperado: "
            f"{executable}"
        )
    executable.replace(versioned_executable)
    executable = versioned_executable

    if sys.platform.startswith("win"):
        analysis = build_dir / "Conector_V" / "Analysis-00.toc"
        analysis_text = analysis.read_text(encoding="utf-8", errors="replace")
        if "('win32timezone'," not in analysis_text or "win32file.pyd" not in analysis_text:
            fail("El ejecutable no incluyó win32timezone/win32file; no se empaquetará")

    # Utilidad de consola independiente; no abre la interfaz ni emite facturas.
    PyInstaller.__main__.run([
        str(base_dir / "fiscal_sequence.py"), "--name", "SecuenciaFiscal",
        "--onefile", "--console", "--noconfirm", "--noupx",
        "--distpath", str(dist_dir), "--workpath", str(build_dir / "sequence_admin"),
        "--specpath", str(base_dir),
    ])
    admin_executable = dist_dir / "SecuenciaFiscal.exe"
    if not admin_executable.is_file():
        fail("No se generó SecuenciaFiscal.exe")

    dist_config = dist_dir / "config.ini"
    if dist_config.exists():
        print(f"[INFO] Configuración existente conservada: {dist_config}")
    else:
        shutil.copy2(config, dist_config)
        print(f"[INFO] Configuración inicial copiada: {dist_config}")
    (dist_dir / "Agente_FF").mkdir(parents=True, exist_ok=True)
    (dist_dir / "Agente_FF" / "Resultado_FF").mkdir(parents=True, exist_ok=True)

    package = dist_dir / f"Conector_V{version}.zip"
    with zipfile.ZipFile(
        package,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.write(executable, executable.name)
        archive.write(admin_executable, admin_executable.name)
        # Distribuir solamente plantilla, nunca la configuración de una caja.
        archive.write(base_dir / "config.example.ini", "config.ini")
        # Mantener juntas las guías y sus enlaces en el paquete de instalación.
        for guide in sorted(base_dir.glob("*.md")):
            archive.write(guide, guide.name)
        archive.writestr("Agente_FF/", "")
        archive.writestr("Agente_FF/Resultado_FF/", "")
    package_hash = hashlib.sha256(package.read_bytes()).hexdigest().upper()
    (dist_dir / f"Conector_V{version}.sha256.txt").write_text(
        package_hash + "\n",
        encoding="ascii",
    )

    print("\n" + "=" * 72)
    print("[OK] Conector compilado correctamente")
    print(f"[OK] Ejecutable: {executable}")
    print(f"[OK] Paquete: {package}")
    print(f"[OK] SHA-256 del paquete: {package_hash}")
    print(f"[OK] Configuración: {dist_config}")
    print(f"[OK] Carpeta TXT: {dist_dir / 'Agente_FF'}")
    print("=" * 72)


if __name__ == "__main__":
    main()
