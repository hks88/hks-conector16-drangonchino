# Contador fiscal local y comandos

Versión documentada: 5.2.8. Para una instalación nueva seguir primero
[Instalación limpia](INSTALACION_LIMPIA.md). Para una instalación usada,
[respaldar y conciliar](RESPALDO_Y_RECUPERACION.md) antes de intervenir.

## Identidad y persistencia

El modo local conserva las asignaciones y respuestas en `fiscal_state.sqlite3`.
El perfil tiene siete dimensiones: RUC emisor, ambiente, sucursal, punto,
base Odoo, ID de compañía e ID de TPV. Todas deben coincidir entre el INI,
la vinculación del módulo y el estado persistido. El Agente debe operar con
el mismo perfil fiscal. El RUC del comprador no selecciona al emisor.

`fiscal_local_activated.json` registra la activación. Si falta el SQLite pero
queda la marca, el programa bloquea la inicialización. Es una protección
ante pérdida de estado; borrar la marca no es una recuperación válida.

Usar una carpeta y una instalación emisora activa por perfil. La base local
no coordina números con otra PC, otro TPV o un sistema externo. Crear otro
TPV con el mismo emisor/punto no crea un rango fiscal independiente en FF.

## Comandos disponibles

Desde PowerShell, situado en la carpeta operativa:

```powershell
.\SecuenciaFiscal.exe --help
.\SecuenciaFiscal.exe status
```

La utilidad admite únicamente las acciones `init`, `status` y `backup`.
`--config` permite indicar el INI; el SQLite se ubica junto a ese archivo.
Usar una ruta explícita evita leer por error el INI de otra instalación:

```powershell
.\SecuenciaFiscal.exe status --config 'C:\Conector\config.ini'
```

No hay comandos de reset, restauración, renumeración o actualización forzada.
Una salida distinta de cero indica fallo: detener el procedimiento y conservar
el mensaje. No continuar con pasos que dependan de una operación fallida.

## Inicializar una sola vez

Antes de `init`, detener el conector y confirmar con FF el historial de ese
perfil y las colas pendientes de todos los sistemas que lo usan. El portal
puede mostrar documentos autorizados sin mostrar reservas o envíos inciertos.
No elegir el inicio usando solo la última venta que aparece en Odoo.

Esta es una **plantilla de comando**, no una instrucción con números de inicio
predeterminados. Sustituir ambos marcadores por enteros conciliados:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\Conector'
    .\SecuenciaFiscal.exe init --last-invoice ULTIMA_FACTURA_CONFIRMADA --last-credit ULTIMA_NC_DEVOLUCION_CONFIRMADA --confirm-ff-and-queues
    if ($LASTEXITCODE -ne 0) { throw 'Falló la inicialización. No continuar.' }
    .\SecuenciaFiscal.exe status
    if ($LASTEXITCODE -ne 0) { throw 'Falló la consulta del estado.' }
}
```

| Parámetro | Valor que representa |
|---|---|
| `--last-invoice` | Último número de factura confirmado para el perfil |
| `--last-credit` | Mayor último número entre NC/devoluciones del flujo, incluidos tipos `06` y `09` |
| `--confirm-ff-and-queues` | Confirmación del operador de que concilió FF y colas; no es una consulta automática |

Los dos números son **últimos utilizados**, no siguientes. El contador común
se inicializa en `max(last_invoice, last_credit)` y una nueva reserva usa el
siguiente número. Se reciben enteros sin separadores; el documento se formatea
a diez dígitos. No existe opción separada `--last-debit`; si hay otros tipos o
sistemas, resolver su continuidad con FF antes del corte.

Si nunca existieron NC/devoluciones de ese perfil y tampoco hay pendientes,
corresponde `--last-credit 0`. Ambos parámetros pueden ser cero solo en una
identidad sin uso, confirmado con FF. Una PC nueva, una sesión nueva o una
reinstalación no demuestran que la identidad sea nueva.

`init` rechaza un SQLite existente o una activación previa detectada. No se
repite para actualizar el EXE, encender la PC o corregir un rechazo. Si ya se
operó con una inicialización equivocada, suspender nuevas emisiones y hacer
una conciliación asistida; no bajar el valor editando tablas.

## Cómo interpretar status

| Campo | Significado |
|---|---|
| Siete campos del perfil | Identidad con la que se abrió el estado |
| `last_number` | Mayor número reservado en el contador común |
| `states.prepared` | Registros preparados, aún sin reclamar su despacho |
| `states.dispatched` | Despacho reclamado; puede existir envío o resultado incierto |
| `states.responded` | Respuesta almacenada, tanto de éxito como de rechazo |

`responded` no significa necesariamente autorizado en FF ni sincronizado
con Odoo. Una reserva no se libera por un error del Agente. Una ausencia de
`dispatched` tampoco prueba que no existan pedidos pendientes en el navegador.

Los estados se conservan para reutilizar la identidad de la misma venta.
El UID fiscal, su número y su contenido deben permanecer vinculados. Un cambio
de venta después del despacho se bloquea para evitar asociar otra operación
al mismo documento. La corrección de pagos mientras está `prepared` requiere
un módulo compatible y la autorización del protocolo; no permite cambiar
productos, totales o identidad libremente.

## Límite de diez dígitos

El rango técnico es `0000000001` a `9999999999`. El contador no vuelve a cero
al llegar al máximo. Las facturas y devoluciones del flujo comparten el rango.
Planificar la continuidad con anticipación; la herramienta no genera alertas
automáticas de agotamiento.

Si el número heredado de Odoo supera el formato, no recortarlo. Si el contador
local agota el rango real, no reiniciarlo. Son situaciones diferentes:
ver [Secuencias y cambio de punto](SECUENCIAS_Y_CAMBIO_PUNTO.md).

## Respaldo y pendientes

`backup` crea una copia consistente del SQLite y exige un destino que aún
no exista. La carpeta de destino debe estar creada. No incluye INI, Agente,
marca de activación ni cola del navegador. Ver el
[procedimiento completo de respaldo](RESPALDO_Y_RECUPERACION.md).

Un resultado tardío puede existir en `Agente_FF` o `Resultado_FF` aunque el
estado siga `dispatched`. La consulta de estado no importa esos archivos.
El reintento compatible de la misma solicitud puede recuperar una respuesta
existente sin reservar otro número; no se garantiza conciliación automática.
Para recuperarlo conservar el UID y el documento originales y seguir la
[guía de incidencias](SOLUCION_DE_PROBLEMAS.md).
