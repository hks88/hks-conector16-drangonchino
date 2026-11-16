# Diagnóstico de latencia

El tiempo hasta la respuesta del POS incluye entrega por Socket.IO,
preparación/reserva local, publicación del TXT, espera del Agente y guardado
de la respuesta. La sincronización del pedido con el servidor Odoo y la
autorización posterior son etapas distintas.

La versión 5.2.8 registra estos indicadores en `log.txt`:

```text
Tiempo fiscal | ... | preparar_ms=... | buscar_previa_ms=... | reservar_envio_ms=... | publicar_ms=... | esperar_ff_ms=... | guardar_ms=... | total_ms=...
ACK fiscal listo | ... | total_ms=...
```

Comparar varias operaciones y distinguir preparación, búsqueda previa,
reserva/envío, espera del Agente y guardado. `esperar_ff_ms` comienza después
de publicar y termina con una respuesta completa; no es una medición exclusiva
de Internet o de impresión. `total_ms` puede incluir trabajo adicional entre
etapas; no asumir que todos los campos enumeran el tiempo completo.

En modo local una solicitud nueva sondea la carpeta activa. Los reintentos
pueden buscar también en el archivo histórico. La persistencia y las
comprobaciones de identidad forman parte de la protección del documento;
no desactivarlas para reducir milisegundos.

Desde las fuentes se puede observar actividad durante operaciones reales ya
autorizadas por el operador, sin crear ventas automáticas. Abrir PowerShell
en la raíz del repositorio y ajustar la ruta:

```powershell
.\tools\Medir-Latencia-FF.ps1 -DirectorioConector 'C:\Conector' -DuracionSegundos 300
```

El observador lee nombres, tamaños y tiempos de archivos y guarda un CSV y un
extracto del log en el Escritorio por defecto. No publica TXT ni modifica el
contador. Esas salidas pueden contener datos de operación: mantenerlas privadas
y fuera del repositorio. Los archivos incompletos o eventos perdidos requieren
contrastar con los logs; un observador de archivos no prueba autorización.

El script es una herramienta de fuentes y no tiene por qué acompañar un ZIP
de ejecutables. Si Windows bloquea scripts, seguir la política del administrador;
no cambiar globalmente la política de ejecución para esta medición.

Antes de atribuir una demora al conector, contrastar hora/zona horaria, carga
de la PC, disponibilidad del Agente y tiempos del servidor. Una prueba simulada
sirve para aislar una etapa, no para prometer el tiempo real de una instalación.
