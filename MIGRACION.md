# Actualización y migración de instalaciones existentes

## Compatibilidad de protocolo

| Módulo Odoo | Conector | Modo | Alcance |
|---|---|---|---|
| 4.0 original | 5.2.8 | `odoo` | Protocolo heredado; el conector rechaza números fuera del formato válido |
| 4.1.5 | 5.1.9 | Heredado | Protocolo anterior; no sirve para activar contador local |
| 4.1.5 | 5.2.8 | `odoo` | Numeración suministrada por Odoo y diario heredado |
| 4.1.5 | 5.2.8 | `local` | Protocolo 2 y perfil/contador local; sucursal alfanumérica admitida |
| 4.1.4 | 5.2.8 | `local` | Perfil numérico ya compatible; no habilita letras si el módulo las rechaza |
| Módulo que emite heredado | Conector en `local` | Incompatible | El conector no cambia de protocolo automáticamente |
| Módulo en local | Conector sin protocolo 2 | Incompatible | No habilitar nuevas emisiones con esa combinación |

La matriz describe el código/protocolo, no certifica instaladores ni todos
los módulos adicionales de Odoo. Las versiones posteriores de un módulo deben
validarse de forma específica. Los fixtures de tests no son módulos instalables.

Un INI antiguo sin sección `[FiscalSequence]` se interpreta como `odoo`.
Si la sección existe, `modo` debe ser explícitamente `odoo` o `local`.
La presencia de SQLite local, WAL/SHM o marca bloquea el retorno a heredado.

## Actualizar el EXE sin cambiar el modo

1. Proteger los pedidos pendientes del navegador y hacer un respaldo completo.
2. Detener el conector. Conservar `config.ini`, bases, marcas y `Agente_FF`.
3. Instalar el software compatible sin sustituir el INI con la plantilla.
4. Actualizar el acceso de arranque y evitar ejecutar dos versiones.
5. En local, comprobar `status` con el mismo perfil. No ejecutar `init`.
6. Verificar las ventas/pedidos y la autorización según el flujo del entorno.

Si se usa un actualizador, verificar primero su comportamiento de copia y
respaldo con datos simulados. No confundir un proceso/puerto sano con emisión
correcta ni suponer que el actualizador activa local o actualiza Odoo.

## Migrar de heredado a local

1. Validar el módulo compatible en Odoo. Una actualización del servidor afecta
   a la base compartida y exige coordinar servicio y assets; las activaciones
   posteriores se organizan por TPV.
2. Detener nuevas operaciones del TPV y proteger/conciliar sus pendientes.
   Los pedidos anteriores no se convierten ni se renumeran por cambiar el modo.
3. Con la sesión cerrada, completar el perfil correcto en Odoo y preparar esa PC
   y su Agente. La identidad se copia de la configuración del TPV.
4. Conciliar los últimos números vigentes y las reservas con FF y todos los
   sistemas que compartan perfil. No usar un máximo obtenido de otra caja.
5. Configurar `modo = local`, inicializar una sola vez si corresponde a una
   primera activación legítima y comprobar `status`.
6. Iniciar el conector y cargar los assets correctos del POS, conservando el
   almacenamiento del navegador. Verificar el flujo primero en pruebas.

Ver [Instalación](INSTALACION_LIMPIA.md) y [Contador](CONTADOR_LOCAL.md). Si el
rango fiscal ya está agotado, la migración no permite reiniciarlo: seguir
[Secuencias](SECUENCIAS_Y_CAMBIO_PUNTO.md).

## Estados antiguos, cambio de perfil y reversión

El esquema local esperado es `2`. Los estados de versiones con otro esquema
se rechazan y no se migran automáticamente. No borrar su SQLite o su marca
para conseguir que una versión nueva arranque; preparar una migración asistida.

En el módulo base, un TPV que activó local bloquea cambiar su identidad o
volver al modo anterior. Otro punto requiere otro perfil válido y el proceso
de cambio correspondiente. Una PC activada en local no debe arrancar un binario
antiguo que desconozca sus protecciones.

Para revertir código, preservar y conciliar antes los pendientes y el estado
actual. No reducir la numeración ni restaurar un respaldo fiscal antiguo para
acompañar una versión anterior del EXE.
