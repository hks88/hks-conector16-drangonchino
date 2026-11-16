# Conector Odoo POS → Factura Fácil para Windows

Versión incluida: **5.2.8**

Conecta un TPV de Odoo 16 con un Agente Factura Fácil instalado en la misma PC.
Recibe solicitudes por Socket.IO en `localhost:3222`, entrega archivos al Agente
y devuelve su respuesta al pedido original.

El repositorio contiene fuentes, recursos, pruebas y guías. El Agente FF, el módulo
Odoo instalable, los certificados y los ejecutables se distribuyen por separado.
`dist` se genera al compilar y está excluido de Git.

## Documentación

| Necesidad | Guía |
|---|---|
| Instalar por primera vez | [Instalación limpia](INSTALACION_LIMPIA.md) |
| Vincular identidad e inicializar | [Contador local y comandos](CONTADOR_LOCAL.md) |
| Alcanzar un límite o cambiar el punto fiscal | [Secuencias y cambio de punto](SECUENCIAS_Y_CAMBIO_PUNTO.md) |
| Resolver pendientes, desconexiones y rechazos | [Solución de problemas](SOLUCION_DE_PROBLEMAS.md) |
| Respaldar, reinstalar o reemplazar una PC | [Respaldo y recuperación](RESPALDO_Y_RECUPERACION.md) |
| Actualizar una instalación existente | [Migración y compatibilidad](MIGRACION.md) |
| Compilar desde las fuentes | [Compilación en Windows](COMPILAR_WINDOWS.md) |
| Medir tiempos | [Diagnóstico de latencia](DIAGNOSTICO_LATENCIA.md) |
| Preparar el repositorio y las pruebas | [Paquete para GitHub](PAQUETE_GITHUB.md) |

## Modos de numeración

- **`odoo` / heredado:** recibe el número calculado por Odoo. Rechaza números fuera
  del formato de diez dígitos; no los recorta ni asigna otros.
- **`local`:** asigna un contador persistente en `fiscal_state.sqlite3`. Requiere un
  perfil completo, inicialización conciliada y módulo Odoo compatible con protocolo 2.

Una instalación que ya activó local conserva esa identidad. Cambiar de modo,
punto, compañía o PC requiere el procedimiento de la guía correspondiente.
Un número de sesión de Odoo no es un número fiscal ni el ID de un TPV.

El perfil incluye base Odoo, compañía, TPV, RUC emisor, ambiente, sucursal y punto.
Debe coincidir con el Agente FF. Una sola instalación activa debe ser responsable
de cada identidad fiscal; este programa no coordina contadores entre PCs o bases.

## Funcionamiento sin conexión y límites

Con el POS ya cargado y los servicios locales disponibles, la comunicación
POS–conector usa localhost. Si Odoo/Internet no está disponible, el POS debe
conservar su cola para sincronizar posteriormente. Las capacidades de emisión
y autorización del Agente/PAC son independientes del conector.

Una respuesta del Agente, una venta registrada en Odoo y una autorización fiscal
son confirmaciones distintas. Esta versión no conserva una copia completa de la
cola Odoo fuera del navegador, no garantiza su recuperación tras borrar datos
del sitio y no concilia automáticamente respuestas tardías.

El programa no es un producto oficial de Factura Fácil ni de la DGI. Las pruebas
con componentes simulados no sustituyen la validación del módulo, del Agente y de
los documentos usados en cada instalación.

## Verificación de fuentes

Después de preparar el entorno descrito en la guía de compilación:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/test_odoo_fiscal.cjs
```

Los datos de las pruebas son simulados. No se incluyen perfiles de clientes,
documentos reales, bases fiscales ni registros de instalaciones.
