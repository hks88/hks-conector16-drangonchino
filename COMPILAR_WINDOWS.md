# Compilar en Windows

La versión de este repositorio es 5.2.8. Usar Windows x64, Python con sintaxis
3.10+ y un entorno virtual nuevo; validar las versiones de dependencias en
el entorno de compilación. Se necesita Node.js para las pruebas JavaScript.
Las dependencias declaradas no constituyen un bloqueo reproducible de todas
las versiones transitivas.

## Preparación y compilación

Ejecutar desde la raíz de un checkout nuevo. La plantilla local debe seguir
vacía para compilar; configurar los datos reales después, en la instalación.
El siguiente bloque se detiene ante errores y ante un INI ya existente:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    if (Test-Path -LiteralPath '.\config.ini') {
        throw 'Ya existe config.ini. Use un checkout limpio para compilar.'
    }
    py -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno virtual.' }
    .\.venv\Scripts\python.exe -m pip install -r .\requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Falló la instalación de dependencias.' }
    Copy-Item -LiteralPath '.\config.example.ini' -Destination '.\config.ini'
    .\.venv\Scripts\python.exe .\compilar.py
    if ($LASTEXITCODE -ne 0) { throw 'Falló la compilación. No distribuir las salidas.' }
}
```

Si una ejecución falla y deja un INI, revisarlo antes de volver a preparar el
checkout. No sustituir la configuración operativa de una caja. El compilador
requiere `config.ini`, pero el ZIP distribuible incorpora la plantilla
`config.example.ini`, nunca los valores locales del INI.

Se generan localmente:

- `dist/Conector_V5.2.8.exe` y `dist/SecuenciaFiscal.exe`.
- `dist/Conector_V5.2.8.zip`, su archivo SHA-256 y todas las guías Markdown.
- Estructura vacía de `Agente_FF/Resultado_FF`; el Agente FF se instala aparte.

Los ejecutables se compilan sin UPX. En Windows se comprueba la inclusión de
`win32timezone` y `win32file`. Los `.spec`, `build` y `dist` son salidas locales;
pueden contener rutas del compilador y no se publican en el repositorio.
No reutilizar una salida antigua si la compilación nueva no terminó bien.

## Pruebas de fuentes

```powershell
& {
    $ErrorActionPreference = 'Stop'
    .\.venv\Scripts\python.exe -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Fallaron pruebas Python.' }
    node --test tests/test_odoo_fiscal.cjs
    if ($LASTEXITCODE -ne 0) { throw 'Fallaron pruebas JavaScript.' }
}
```

Las pruebas usan datos y servicios simulados. Los archivos de módulo Odoo en
`tests/fixtures` son referencias parciales para pruebas, no un addon instalable.
Las herramientas que prueban EXE necesitan compilarlos primero y usar carpetas
aisladas; no deben ejecutarse contra una instalación operativa.

## Verificar el paquete generado

Calcular la huella y compararla con el archivo generado junto al ZIP:

```powershell
Get-FileHash -LiteralPath '.\dist\Conector_V5.2.8.zip' -Algorithm SHA256
Get-Content -LiteralPath '.\dist\Conector_V5.2.8.sha256.txt'
```

Una huella sirve para comprobar integridad respecto de un origen confiable;
no es una firma de editor ni una aprobación de Microsoft. Inspeccionar el ZIP
antes de distribuirlo: solo software, guías, plantilla vacía y estructura de
carpetas, sin SQLite, documentos, colas, logs o credenciales.

La compilación no despliega Odoo ni configura FF. Seguir
[Instalación limpia](INSTALACION_LIMPIA.md) o [Migración](MIGRACION.md) según
el caso. Cada cambio de fuentes necesita sus pruebas y validación del EXE.
