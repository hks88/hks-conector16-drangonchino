# Solución de problemas operativos

Aplicable al conector 5.2.8. Distinguir siempre tres hechos: respuesta del
Agente, autorización fiscal y registro del pedido en Odoo. Ninguno demuestra
por sí solo los otros dos. Consultar la identidad y el documento correctos.

## Antes de intervenir

Conservar el pedido original, UID fiscal, número, importe, fecha, sesión,
respuesta y archivos del Agente. Hacer un [respaldo](RESPALDO_Y_RECUPERACION.md)
y proteger la cola del navegador. No borrar caché/datos del sitio, reiniciar
contadores ni crear otra venta para resolver un resultado incierto.

Cuando el resultado de una venta sea incierto, resolver esa operación antes
de reintentar su emisión. Si la identidad o el estado completo están en duda,
suspender nuevas emisiones de ese perfil mientras se concilia.

## Guía rápida

| Síntoma | Comprobar primero | Procedimiento |
|---|---|---|
| POS no conecta con la PC | Proceso correcto, carpeta, log, puerto local 3222 | Servicios y comunicación local |
| Vuelve Internet y aparece «Documento fiscal pendiente» | UID original, número reservado, contenido y estado | Caída de Internet / respuesta tardía |
| FF tiene una factura y Odoo no | Contexto de compañía, número/CUFE, sesión y cola del navegador | Conciliación FF–Odoo |
| «La venta cambió…» | Datos originales y estado fiscal de ese UID | Cambios después de reservar |
| Rechazo de formas de pago | Payload original, importes y mapeo del medio de pago | Pagos y ventas a cero |
| Resultado en carpeta activa | Antigüedad, operación activa, contenido y estado | Archivos de resultados |
| Identidad no coincide / falta estado | INI, siete campos, SQLite y marca | Identidad y recuperación |
| Límite o número largo | Modo y `last_number` | [Secuencias](SECUENCIAS_Y_CAMBIO_PUNTO.md) |
| Falló impresión/gaveta | Documento ya emitido y periférico local | Periféricos |
| Antivirus eliminó un EXE | Origen, hash, nombre y detalle de detección | Antivirus |

## Servicios y comunicación local

Abrir el POS en la PC donde corren el conector y el Agente. En otra PC,
`localhost` apunta a esa otra máquina. Confirmar acceso de arranque y versión;
evitar procesos duplicados o un EXE antiguo desde otra carpeta.

```powershell
Test-NetConnection -ComputerName 127.0.0.1 -Port 3222
Get-Content -LiteralPath 'C:\Conector\log.txt' -Tail 100
```

Un puerto abierto solo comprueba conectividad TCP; un log «Odoo conectado»
comprueba una conexión, no emisión ni sincronización. Revisar errores de
perfil, estado, dependencias y permisos antes de reiniciar servicios.
No cambiar exposición del puerto/firewall para resolver una instalación
que debe comunicarse localmente.

## Caída de Internet/Odoo y respuestas tardías

Con el POS ya cargado, su comunicación con el conector usa localhost.
Mantener la funcionalidad offline exige conservar los pedidos en el mismo
perfil del navegador y mantener disponibles el conector, el Agente y sus
carpetas. La autorización sin Internet depende del servicio de FF; el
conector no añade una autorización offline propia.

Al recuperar la conexión:

1. No modificar productos, pagos, cliente o importe de una solicitud que ya
   pudo enviarse. Conservar UID y número originales.
2. Revisar la cola original del POS y el resultado del Agente, tanto en la
   carpeta activa como en `Resultado_FF`. Consultar `status` sin editarlo.
3. Confirmar en FF si existe el documento, su importe, CUFE y autorización.
4. Si el pedido original conserva el mismo contenido, un reintento admitido
   por el módulo puede reutilizar la respuesta guardada o encontrar una
   respuesta existente. No debe generar otro UID ni reservar otro número.
5. Si aparece un bloqueo, no insistir cambiando la venta ni duplicándola.
   Conservar las evidencias y resolver con el administrador si falta una
   respuesta local o la sincronización con Odoo.
6. Después de sincronizar, comprobar **un solo pedido** en Odoo con el mismo
   documento y sus pagos correctos. Conservar el respaldo hasta conciliar.

En 5.2.8 la consulta de estado lee SQLite; no importa respuestas tardías.
El archivador de resultados tampoco confirma pedidos en Odoo. Esta versión
no tiene un servicio de conciliación automática ni una cola de pedidos Odoo
completa fuera del navegador. Instalar el EXE no sustituye una corrección
compatible del módulo ni recupera datos del navegador que ya se borraron.

## FF tiene un documento que no aparece en Odoo

Una lista filtrada, un reporte de productos o una consulta que falló no
prueban ausencia. Antes de registrar una recuperación:

1. Verificar base, compañía, TPV, zona horaria, día y sesión correctos.
2. Buscar por número fiscal, CUFE, UID y referencia; revisar sesiones originales,
   de rescate y pedidos ya importados, incluidos estados distintos de pagado.
   Resolver errores de acceso, paginación y consultas incompletas.
3. Buscar pendientes en el navegador original con el que se vendió; otra PC o
   perfil no permite concluir que allí no hay una cola pendiente.
4. Comparar las líneas, impuestos, cliente, total, recibido y cambio con el
   documento efectivamente emitido. `fiscal_state.sqlite3` no sustituye todos
   los datos del pedido Odoo.
5. Si la cola conserva la venta, preferir su sincronización normal compatible,
   conservando identidad y resultado. No emitir otra factura.
6. Solo con la ausencia confirmada y datos suficientes, preparar una recuperación
   administrativa que registre la venta y el pago **una sola vez**, reutilice
   el número/CUFE originales y no invoque emisión. Respaldar y comprobar antes
   y después que no existen duplicados. Esa recuperación necesita un
   procedimiento específico; no hay un script genérico incluido aquí.

Si la sesión original está cerrada y contabilizada, no reabrirla ni insertar
registros directamente en SQL. El administrador debe determinar la sesión
admitida y el tratamiento de pagos, stock y contabilidad. Una recuperación
no vuelve a cobrar al cliente ni reescribe el documento fiscal.

El QR/CUFE puede consultarse para verificar la autorización, según la
[guía de consulta de la DGI](https://dgi.mef.gob.pa/_7FacturaElectronica/fpreguntas).
`processed` o `responded` en el conector no bastan para esa verificación.

## Cambios después de reservar un número

El mensaje «La venta cambió…» protege la asociación entre una venta y su
identidad fiscal. Puede deberse a una modificación real o a datos distintos
en un reintento; el mensaje por sí solo no determina la causa.

- Si todavía no hay reserva, corregir la venta antes de solicitar emisión.
- Mientras está `prepared`, el protocolo compatible permite algunas correcciones
  de pagos conservando número y fecha originales; no habilita cambiar libremente
  productos, cliente, total, tipo o identidad.
- En `dispatched`, pudo existir envío al Agente incluso si no aparece aún en
  el portal. No desbloquearlo borrando estado ni republicando TXT manualmente.
- En `responded`, examinar la respuesta: puede ser éxito o rechazo. Un rechazo
  no libera automáticamente el número ni autoriza a sustituir su contenido.

Recuperar los datos originales cuando sea posible y conciliar con FF antes de
corregir un documento emitido. No se incluyen comandos para forzar cambios.

## Pagos, cortesías y ventas a cero

Ante errores de formas de pago, revisar el código y nombre recibidos desde
Odoo y el documento enviado al Agente, no solo el nombre visible del método.
Comprobar suma de pagos, importe de la venta y cambio. Corregir la configuración
del medio de pago no modifica automáticamente una solicitud ya despachada.

Un total `0.00` no demuestra que exista una forma de pago válida ni que el
Agente acepte ese payload. Validar en pruebas el flujo exacto con el módulo
y el Agente utilizados y definir con FF y el responsable contable cómo se
representa una cortesía. El conector no decide su tratamiento tributario.

Para inventario y trazabilidad, conservar los productos realmente entregados
y el motivo de la cortesía según el flujo aprobado. No simular efectivo,
cobrar un centavo ficticio ni cambiar un documento rechazado para que pase
sin resolver antes su estado. Un producto genérico a cero por sí solo no
registra las existencias de los alimentos entregados.

## Archivos de resultados

El Agente genera `result_NUMERO_n`. Que quede un archivo en la raíz puede
ser transitorio: el observador espera antigüedad suficiente y respeta las
órdenes activas antes de archivarlo en `Resultado_FF`. No es una garantía de
que siempre deba quedar exactamente uno ni de que el pedido esté sincronizado.

Si permanece o crece la cantidad, revisar actividad del observador, permisos,
contenido completo, fecha y pedidos pendientes. No moverlo ni borrarlo para
«limpiar» mientras una solicitud puede necesitarlo. Un archivo con texto de
error es una respuesta de rechazo, no un documento autorizado.

## Identidad, estado y cambio de PC

Comparar los siete campos con `status` y la vinculación del módulo. Conservar
ceros de sucursal/punto y el nombre exacto de base. No cambiar IDs para sortear
un error de identidad. Un TPV local usado puede bloquear su edición.

Si falta/corrompe SQLite, hay una marca de activación o solo existe una copia
antigua, seguir [Respaldo y recuperación](RESPALDO_Y_RECUPERACION.md).
Para otro punto/sucursal/perfil usar [Cambio de punto](SECUENCIAS_Y_CAMBIO_PUNTO.md).

## Impresora y gaveta

Si ya existe el documento fiscal, diagnosticar el periférico y reimprimir el
mismo comprobante desde el flujo admitido. No repetir emisión para abrir la
gaveta o sacar una copia. Revisar nombre de impresora en Windows, puerto COM,
dispositivo disponible y política `efectivo`/`siempre` del INI.
Una respuesta reutilizada no debe volver a abrir la gaveta como otra venta.

## Antivirus

Conservar nombre de amenaza, fecha, ruta, hash y procedencia del archivo.
Comparar su huella con la distribución confiable y revisar el equipo antes de
considerarlo un falso positivo. Un EXE sin firma o una clasificación por
aprendizaje automático no prueban por sí solos que el archivo sea seguro.

Presentar el archivo afectado a Microsoft para revisión desde su
[portal oficial de envíos](https://www.microsoft.com/en-us/wdsi/filesubmission).
Comprobar el resultado y actualizar la protección antes de volver a probar.
No desactivar Defender ni excluir toda la carpeta como procedimiento habitual.
La copia del código y las bases fiscales deben permanecer resguardadas.

## Evidencias para soporte

Preparar una copia privada con versión de conector/módulo, modo, hora y zona
horaria, etapa fallida, UID/número, estado, resultado y consulta Odoo completa.
Separar hechos confirmados de hipótesis. El log de la PC, el del VPS y la
información de FF describen etapas diferentes; no atribuir una pérdida a
Internet únicamente porque coincidió con una caída.

Al compartir públicamente, anonimizar RUC, nombres, base, CUFE/QR, documentos,
credenciales y rutas personales. No publicar SQLite, INI real o logs completos.
