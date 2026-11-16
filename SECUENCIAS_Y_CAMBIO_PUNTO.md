# Límites de secuencia y cambio de punto de facturación

Estos procedimientos describen la versión 5.2.8. No hay un comando automático
que cree series en FF, cambie la identidad del TPV o reinicie un contador usado.

## 1. Identificar qué límite se alcanzó

| Situación | Cómo distinguirla | Acción |
|---|---|---|
| Número heredado de Odoo con más de 10 dígitos | El conector está en `modo = odoo`; el número recibido ya no tiene el formato admitido | Conservar la venta y planificar migración a local con el corte real de FF, si el perfil aún tiene rango |
| Contador local próximo al máximo | `status` muestra un `last_number` cercano a `9999999999` | Preparar continuidad con FF antes de agotarlo |
| Rango local agotado | `last_number = 9999999999` y una solicitud nueva recibe «Rango fiscal agotado» | Detener nuevas emisiones de ese perfil, conciliar y coordinar la identidad siguiente |
| FF informa otro límite o rechaza secuencia | El estado local puede estar dentro de diez dígitos | Investigar restricciones/desfase del proveedor; no usar el límite técnico como autorización |
| Código de punto ya en `999` | Es el código del punto, no el número de documento | No generar `1000`; coordinar otro perfil admitido con FF |

El límite técnico implementado es un número de documento entre
`0000000001` y `9999999999`, y un punto entre `001` y `999`. Son límites del
programa; no autorizan por sí solos el uso de una serie o un punto en FF.

## 2. Número heredado demasiado largo

El modo heredado no usa el contador local. Un número calculado con datos de
sesión puede desbordar su formato aunque el punto fiscal todavía tenga rango.
Crear otra sesión de Odoo no equivale a obtener otra serie fiscal.

1. Conservar la venta original y determinar si llegó a FF antes de intentar
   otra operación. Consultar colas, respuestas y portal para el perfil correcto.
2. No recortar el número, tomar sus últimos diez dígitos ni cambiarlo a mano.
3. Si el perfil aún tiene rango, preparar la [migración a local](MIGRACION.md)
   con módulo compatible y máximos vigentes conciliados.
4. Resolver por separado las ventas anteriores: cambiar el modo no transforma
   ni renumera automáticamente pedidos pendientes.

Si FF ya utilizó realmente el máximo, pasar a la continuidad del apartado 4;
migrar a local no crea más capacidad para la misma identidad.

## 3. Vigilancia del contador local

En la carpeta de instalación:

```powershell
.\SecuenciaFiscal.exe status
```

Capacidad técnica restante: `9999999999 - last_number`. Esta cifra cuenta
reservas del contador común, no únicamente facturas autorizadas ni ventas
registradas en Odoo. Las devoluciones del flujo también consumen esa secuencia.

Programar la continuidad con tiempo según el ritmo de emisión y los trámites
de FF. La utilidad no genera alertas automáticas de capacidad. Al agotarse,
las solicitudes nuevas con documento se rechazan; la consulta de estado,
el respaldo y el tratamiento del mismo UID ya registrado siguen siendo
operaciones distintas de reservar un número nuevo.

## 4. Continuidad cuando el perfil agota su rango

La continuidad exige una identidad admitida por FF. La documentación del
proveedor permite gestionar cajas/puntos asociados a una sucursal; el código
es de tres dígitos y no se repite dentro de ella.
[Referencia de FF](https://docs.facturafacil.com.pa/books/comenzando/page/panel-de-preferencia).

1. Suspender nuevas emisiones del perfil agotado. Conservar la cola y la última
   venta; no crear duplicados para superar el bloqueo.
2. Respaldar el estado, configuración, marca de activación, documentos del
   Agente y pedidos del navegador. Conciliar reservas, pendientes, rechazos y
   documentos autorizados con Odoo/FF. No reducir el contador porque un máximo
   autorizado del portal parezca menor.
3. Coordinar con FF el alta/habilitación del nuevo punto o la continuidad que
   corresponda. Obtener confirmación del perfil, ambiente y corte. No asumir
   que sumar uno al código del punto basta para habilitarlo.
4. Con la sesión anterior resuelta y cerrada, crear una **nueva configuración
   TPV** para la identidad nueva. En el módulo base, un TPV activado en local
   no permite editar su identidad: no forzar ese cambio en SQL.
5. Conservar la configuración anterior y sus sesiones como historial. Si se
   archiva el TPV, hacerlo después de conciliar y cerrar, sin eliminar pedidos.
6. Preparar una carpeta de conector nueva y separada. Copiar solo el software
   y una plantilla de configuración, **no** el SQLite ni la marca del perfil
   agotado. La carpeta anterior permanece resguardada; no borrarla para
   conseguir que `init` acepte un perfil diferente.
7. Configurar Odoo, conector y Agente con los datos del perfil nuevo. Actualizar
   la vinculación a su nuevo `pos.config.id`; no conservar el ID del TPV anterior
   por parecer una caja físicamente igual. Coordinar la carpeta del Agente y el
   acceso de arranque, manteniendo una sola instalación activa para cada perfil.
8. Inicializar una vez con los últimos números reales del **perfil nuevo**.
   Si ese punto ya tuvo uso desde otro sistema, continuar sus máximos; si FF
   confirma que nunca emitió ni reservó documentos, corresponde el inicio
   nuevo. Ver [Inicialización](CONTADOR_LOCAL.md).
9. Validar primero en pruebas y, en operación, comprobar identidad, número,
   CUFE, autorización y registro en Odoo. Resguardar por separado los respaldos
   de cada perfil.

FF documenta el uso del último secuencial cuando se continúa una operación y
el valor cero para un alta nueva. Esto no prueba que un punto concreto esté
sin uso: debe confirmarse antes de inicializarlo.
[Referencia de FF](https://docs.facturafacil.com.pa/books/comenzando/page/enlace-de-incorporacion).

El respaldo del perfil anterior se conserva también para devoluciones y
documentos pendientes. Si el flujo requiere emitir desde un perfil agotado,
resolver el procedimiento admitido con FF y el administrador; no improvisar
otro punto ni cambiar el documento referenciado.

## 5. Cambiar de punto sin agotar el rango

Cambiar `001` por `002` también cambia la identidad. Aplicar el mismo proceso
de corte, habilitación, TPV nuevo y carpeta separada. No editar únicamente
`punto_de_facturacion` en una instalación local usada: el SQLite y la marca
no coincidirán con la nueva configuración.

Las letras se admiten en **sucursal** (cuatro caracteres en mayúscula), no
en el punto de tres dígitos. Al usar sucursales alfanuméricas comprobar la
compatibilidad del módulo Odoo. Una sucursal tampoco se inventa para eludir
un límite; debe ser un perfil válido en FF.

## 6. Desfase de secuencias

Si el Agente/portal y el estado local no coinciden, conservar ambos y revisar
UID, documento, resultado y pendientes. `last_number` puede estar por delante
de lo autorizado porque representa reservas. Tampoco puede quedar por detrás
de documentos efectivamente usados por otro sistema del mismo perfil.

FF dispone de gestión de corrección y actualización de secuencias por punto,
con últimos números por tipo. Es una gestión del proveedor, no un reset de
SQLite ni una operación del comando `init` sobre una instalación existente.
[Gestión de secuencias FF](https://docs.facturafacil.com.pa/books/comenzando/page/centro-de-gestion-de-cliente).

La utilidad local solo ofrece `init`, `status` y `backup`. No tiene comandos
`reset`, `set-next`, `force`, cambio de punto o restauración. Las correcciones
de un estado usado requieren conciliación y un procedimiento asistido, con
copias consistentes y sin reutilizar números.
