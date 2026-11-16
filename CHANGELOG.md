# Cambios técnicos

## 5.2.8

- Sucursal fiscal de cuatro caracteres ASCII en mayúscula, `A-Z`/`0-9`,
  tanto en modo local como heredado; el punto sigue siendo `001` a `999`.
- El modo local con letras requiere un módulo Odoo que admita ese perfil;
  la base de compatibilidad de pruebas es 4.1.5.
- Conserva el contador persistente y las protecciones del protocolo local 2.
  No migra ni reinicia automáticamente estados usados.

## Capacidades incorporadas en la línea 5.2.x

- Contador común persistente para facturas y devoluciones del flujo, con
  validación de identidad y rango de diez dígitos.
- UID por venta, conservación del TXT exacto, asignación y respuesta.
- Validación de base, compañía, TPV, emisor, ambiente, sucursal y punto.
- Inicialización conciliada, consulta y respaldo mediante `SecuenciaFiscal`.
- Activación explícita local/heredado y protección ante pérdida de SQLite
  después de activar local o intento de regreso al modo anterior.
- Corrección limitada de pagos mientras la solicitud está preparada, con
  módulo compatible; bloqueo de modificaciones tras un despacho posible.
- Búsqueda de resultados por número, sin recorrer el archivo histórico en
  la espera normal de una solicitud nueva; reutilización de respuestas
  existentes sin asignar otra identidad a la venta.
- Diario heredado para evitar sobrescrituras y reenvíos de resultado incierto.
- Tiempos por etapa en el log y apertura de gaveta fuera del camino del ACK.
- Inclusión y comprobación de dependencias Windows para lectura de resultados.

Consultar [Migración](MIGRACION.md) para combinaciones de protocolo. Estas
capacidades no equivalen a autorización fiscal ni a sincronización garantizada
con Odoo.

## Edición de fuentes y documentación

Esta entrega organiza guías generales de instalación limpia, actualización,
secuencias, cambio de punto, respaldos e incidencias. El compilador incorpora
las guías Markdown de la raíz al paquete generado. Las pruebas tienen rutas
internas y datos simulados para ejecutarse desde un checkout independiente.

La versión de ejecución sigue siendo 5.2.8. Esta edición de documentación no
añade conciliación automática de respuestas tardías, recuperación de pedidos
borrados del navegador ni una cola Odoo independiente de él. Ver las
[limitaciones de operación offline](README.md) y la
[guía de incidencias](SOLUCION_DE_PROBLEMAS.md).
