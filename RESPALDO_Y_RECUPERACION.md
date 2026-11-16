# Respaldo, reinstalación y cambio de PC

## Qué conservar

| Elemento | Contenido y utilidad |
|---|---|
| `config.ini` | Identidad, modo, impresora y gaveta de la instalación |
| `fiscal_state.sqlite3` | Contador local, UID, TXT y respuestas; no contiene el pedido Odoo completo |
| `fiscal_local_activated.json` | Marca de activación que evita reinicializar o volver al modo heredado |
| `legacy_dispatch.sqlite3` | Diario de solicitudes del modo heredado, si existe |
| `Agente_FF` y sus subcarpetas | Archivos operativos, resultados y documentos pendientes/procesados |
| Pedidos del navegador | Colas pagadas/no pagadas y evidencias del perfil usado para vender |
| Base/filestore y módulo Odoo | Respaldo del servidor, gestionado por su administrador |

Un respaldo del servidor no incluye el almacenamiento del Chrome de la caja.
Un ZIP de código tampoco es un respaldo fiscal. Guardar las copias con acceso
restringido y fuera del repositorio: pueden contener datos fiscales y personales.

## Respaldo soportado del contador local

En la carpeta de instalación y en modo local:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $carpetaRespaldo = 'C:\RespaldosConector'
    New-Item -ItemType Directory -Path $carpetaRespaldo -Force -ErrorAction Stop | Out-Null
    $destinoFiscal = Join-Path $carpetaRespaldo ('estado-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.sqlite3')
    .\SecuenciaFiscal.exe backup --destination $destinoFiscal
    if ($LASTEXITCODE -ne 0) { throw 'El respaldo fiscal falló.' }
    Get-Item -LiteralPath $destinoFiscal | Select-Object FullName, Length
}
```

La carpeta de destino debe existir y el archivo de respaldo no debe existir:
la utilidad no lo sobrescribe. El comando usa la API de respaldo SQLite; no
reduce el contador ni emite documentos. Después conserva `config.ini` y la
marca, además de los otros elementos necesarios para recuperar la instalación.

Para un corte completo de carpeta, detener nuevas ventas, cerrar el conector y
coordinar el Agente antes de copiar. Si quedan archivos `-wal` o `-shm`, no
descartarlos ni copiar solo el `.sqlite3` de un proceso que siga escribiendo.
`backup` respalda el estado local; no incluye el diario heredado ni la cola del
navegador. La copia completa debe ser consistente y comprobarse aparte.

## Reinstalar o actualizar en la misma PC

1. Proteger pendientes y respaldar el estado completo.
2. Detener el conector y actualizar solo el software mediante un procedimiento
   que conserve configuración, bases, marcas y datos del Agente.
3. Confirmar que el acceso de arranque utiliza la carpeta correcta.
4. Ejecutar `status` para el mismo perfil local antes de habilitar ventas.
5. Continuar con el estado existente. No ejecutar `init` por reinstalar Windows,
   cambiar el EXE, actualizar Odoo o reiniciar el equipo.

Una actualización del conector no actualiza por sí sola el módulo del servidor.
Un actualizador debe comprobarse con estado simulado antes de usarlo sobre una
instalación real; no asumir que respalda o conserva todos los archivos fiscales.

## Sustituir la PC conservando el mismo perfil

1. Detener la PC anterior como emisora y comprobar que no volverá a arrancar
   el conector en paralelo.
2. Conciliar FF, colas y Odoo; conservar los pendientes originales del navegador
   antes de cambiar de equipo o perfil.
3. Preparar el software en la PC nueva. Mantener los siete campos de identidad,
   incluidos el ID del mismo TPV y la base Odoo.
4. Restaurar una copia consistente y vigente del mismo perfil, con configuración,
   estado, marca y documentos necesarios. El respaldo de la utilidad debe quedar
   como `fiscal_state.sqlite3` en la carpeta operativa, no con su nombre de archivo
   de respaldo. Hacerlo con procesos detenidos y supervisión del administrador.
5. Verificar identidad y `last_number`, comparar con lo emitido después del
   respaldo y resolver cualquier diferencia antes de reabrir.

Esta versión no ofrece un comando `restore` ni reconstruye de forma automática
los pedidos completos del navegador. Usar el mismo perfil no significa copiar
su SQLite a una caja adicional que seguirá emitiendo en paralelo.

## Pérdida, corrupción o respaldo antiguo

- **Falta SQLite y hay marca:** se bloquean inicio local e inicialización para
  evitar reiniciar un contador usado. Conservar la marca y buscar un respaldo.
- **Faltan SQLite y marca:** la ausencia de archivos no demuestra un punto
  nuevo. Confirmar su historial en FF y con los demás sistemas antes de decidir.
- **SQLite corrupto:** detener nuevas emisiones y conservar copia íntegra de
  los archivos para diagnóstico. No reparar modificando tablas o borrando filas.
- **Solo existe respaldo antiguo:** identificar documentos/reservas posteriores
  con FF y las colas. No restaurarlo y continuar como si esos números no existieran.
- **El portal tiene más documentos que Odoo:** aplicar la conciliación de
  [Solución de problemas](SOLUCION_DE_PROBLEMAS.md); no emitirlos otra vez.

No restaurar una base Odoo antigua sobre operaciones más recientes para revertir
solo el código del conector. La reversión del software y la continuidad del estado
fiscal deben planificarse por separado.

## Cambio de identidad

Otro RUC, base, compañía, TPV, ambiente, sucursal o punto no es una restauración
del mismo perfil. Seguir [Secuencias y cambio de punto](SECUENCIAS_Y_CAMBIO_PUNTO.md).
Conservar la instalación anterior y preparar la nueva por separado.
