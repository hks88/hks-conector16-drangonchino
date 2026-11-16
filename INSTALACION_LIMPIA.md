# Instalación limpia

Partir del paquete operativo ya extraído, que incluye los ejecutables,
la plantilla de configuración y la estructura de carpetas. Esta guía no
requiere crear carpetas manualmente. Para compilar el ZIP de fuentes de
GitHub, consultar [Compilación en Windows](COMPILAR_WINDOWS.md).

Se necesita el Agente FF configurado y Odoo 16 con un módulo compatible.
La instalación del Agente y del módulo Odoo se gestiona por separado.

## 1. Obtener los datos del TPV

Seleccionar en Odoo la compañía y el TPV que usará esa PC. Configurar el
modo local y copiar los datos de su vinculación Factura Fácil al INI.
Para sucursales alfanuméricas, la base de compatibilidad es el módulo 4.1.5.

| Dato | Valor que debe usarse |
|---|---|
| Emisor | RUC de la empresa configurada en el Agente FF |
| Ambiente | `1` producción o `2` pruebas, igual que en FF |
| Sucursal | Código FF de cuatro caracteres en mayúscula, `A-Z`/`0-9` |
| Punto | Código FF de tres dígitos, de `001` a `999` |
| `odoo_database` | Nombre exacto de la base Odoo |
| `odoo_company_id` | ID interno de la compañía (`res.company`) |
| `odoo_pos_config_id` | ID interno del TPV (`pos.config`) |

La URL del POS contiene `config_id=...`: ese valor identifica el TPV.
`cids` corresponde al contexto de compañía. No utilizar el ID de sesión
ni el número `POS/...` como ID del TPV.

## 2. Completar config.ini

Editar el `config.ini` incluido en el paquete. Sustituir los marcadores
por los datos de la instalación y conservar una sola sección de cada tipo:

```ini
[Target]
gaveta =
sucursal = CODIGO_FF_DE_4_CARACTERES
punto_de_facturacion = CODIGO_FF_DE_3_DIGITOS
Nombre_impresora =
modo_apertura_gaveta = efectivo

[FiscalSequence]
modo = local
emisor = RUC_EMISOR
ambiente = AMBIENTE
odoo_database = BASE_ODOO
odoo_company_id = ID_COMPANIA
odoo_pos_config_id = ID_TPV
```

Si se utilizan, completar `gaveta` con su puerto serial y `Nombre_impresora`
con el nombre exacto de Windows. La política `efectivo` abre la gaveta cuando
corresponde a pagos en efectivo; `siempre` solicita apertura después de cada
procesamiento válido.

El perfil del INI debe coincidir con Odoo y el Agente. Configurar el ambiente
en ambos; el INI del conector no cambia el ambiente del Agente.

## 3. Iniciar el contador en cero

Para un **perfil fiscal nuevo, sin documentos ni reservas anteriores**, abrir
PowerShell en la carpeta del conector, con el conector detenido, y ejecutar:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    .\SecuenciaFiscal.exe init --last-invoice 0 --last-credit 0 --confirm-ff-and-queues
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo inicializar el contador.' }
    .\SecuenciaFiscal.exe status
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo consultar el estado.' }
}
```

El estado inicial mostrará `last_number: 0`. La primera reserva de documento
usará **`0000000001`**. Facturas y devoluciones comparten el contador.
El comando inicializa el estado; no emite documentos.

La opción `--confirm-ff-and-queues` es obligatoria en esta versión de la
utilidad. No realiza una consulta automática a FF ni al servidor Odoo.

Una carpeta o PC nueva puede corresponder a un perfil fiscal que ya operaba.
En ese caso se continúa su numeración: no se inicializa en cero. Si ya existe
SQLite o una marca de activación, no repetir `init`; consultar
[Respaldo y recuperación](RESPALDO_Y_RECUPERACION.md).

## 4. Conciliación opcional

La conciliación es una revisión adicional opcional del historial en FF,
las colas del Agente y los pedidos de Odoo. Permite contrastar la continuidad
del perfil antes de empezar a operar.

Si el perfil tiene historial, utilizar los últimos números usados en lugar
de cero. Plantilla de comando; sustituir los marcadores por enteros:

```powershell
.\SecuenciaFiscal.exe init --last-invoice ULTIMA_FACTURA --last-credit ULTIMA_NC_DEVOLUCION --confirm-ff-and-queues
```

Los valores son los **últimos utilizados**, no los siguientes. El próximo
número será `max(last_invoice, last_credit) + 1`. Si nunca hubo notas de crédito
ni devoluciones, usar `--last-credit 0`. Conservar los números ya reservados
aunque aún no aparezcan autorizados en el portal.

Más detalles en [Contador local](CONTADOR_LOCAL.md) y
[Migración](MIGRACION.md).

## 5. Iniciar la operación

1. Iniciar el Agente FF y comprobar que utiliza el perfil y la carpeta de esta
   instalación.
2. Ejecutar `Conector_V5.2.8.exe` una sola vez.
3. Abrir el POS correspondiente en esa misma PC.
4. Comprobar que las operaciones conservan el mismo número y CUFE en FF y Odoo.

No se repite `init` al encender la PC. Conservar el estado fiscal y los pedidos
pendientes del navegador, también cuando se trabaje sin conexión.
Para límites de numeración o cambio de punto, consultar
[Secuencias](SECUENCIAS_Y_CAMBIO_PUNTO.md); para pendientes o rechazos,
[Solución de problemas](SOLUCION_DE_PROBLEMAS.md).
