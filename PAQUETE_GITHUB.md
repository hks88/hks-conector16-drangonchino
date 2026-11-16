# Preparar y publicar las fuentes

Este paquete contiene el conector Windows 5.2.8, documentación de instalación
y operación, recursos y pruebas. No contiene `dist`, ejecutables, el Agente FF
ni un módulo Odoo completo para instalar. Empezar por [README](README.md).

## Contenido publicable

- Fuentes Python, scripts de compilación y recursos de interfaz.
- `VERSION`, dependencias, `.gitignore` y `config.example.ini` sin datos reales.
- Guías de instalación, migración, secuencias, respaldo e incidencias.
- Pruebas y referencias de protocolo con datos simulados.

Los fixtures de Odoo y versiones heredadas documentan el contrato probado.
Los nombres de sus versiones no indican que sean instalaciones disponibles
para producción. Las rutas de pruebas son internas al repositorio; las
variables opcionales `HKS_ODOO_MODULE` y `HKS_ODOO_JS` permiten probar otro
módulo compatible de forma explícita.

Esta edición mantiene el comportamiento de ejecución del conector 5.2.8.
El empaquetado del compilador incluye todas las guías Markdown de la raíz.
La documentación describe limitaciones actuales; no añade una cola externa
al navegador ni una recuperación automática de respuestas tardías.

## Antes de subir a GitHub

1. Extraer el ZIP de fuentes en un directorio nuevo y revisar sus archivos.
2. Ejecutar las pruebas descritas en [Compilación](COMPILAR_WINDOWS.md).
3. Confirmar que solo se prepara el código que se desea publicar. `.gitignore`
   no protege archivos que ya estuvieran versionados en otro repositorio.
4. Revisar la selección y el historial antes de publicar: excluir INI real,
   SQLite y archivos auxiliares, marcas de activación, colas, logs, documentos,
   exportaciones, certificados, claves, credenciales y datos de diagnóstico.
5. Mantener `dist`, `build`, entornos virtuales, cachés y archivos generados
   fuera del repositorio. Compilar no es un requisito para subir las fuentes.

No usar el repositorio como respaldo de instalaciones. Las carpetas
`respaldos` y `diagnosticos` están ignoradas, pero las copias reales deben
mantenerse en almacenamiento privado y verificable.

Para compartir un diagnóstico, generar una copia reducida y anonimizada.
No adjuntar CUFE/QR reales, documentos ni perfiles completos en un issue
público. Los ejemplos de las guías usan marcadores; nunca deben utilizarse
como cortes de inicialización sin conciliación.

La actualización del conector, la instalación del módulo Odoo y los cambios
de identidad fiscal son procedimientos separados. Publicar estas fuentes
no modifica ninguna caja, contador, pedido ni servidor.
