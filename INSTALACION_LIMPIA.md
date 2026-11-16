# Instalación limpia

Este procedimiento crea una instalación nueva. Si ese emisor y punto ya
operaban desde otra carpeta, PC o sistema, seguir primero
[Migración](MIGRACION.md) y [Respaldo](RESPALDO_Y_RECUPERACION.md): instalar
desde cero no autoriza comenzar otra vez la numeración.

## 1. Preparar el entorno

- Windows 10/11 de 64 bits y carpeta local NTFS con permisos para el operador.
  Ruta ilustrativa de esta guía: `C:\Conector`.
- Odoo 16 con el módulo `hks_pos_offline_FF_gobierno` compatible, instalado
  y validado por el administrador. El ZIP de fuentes del conector no instala
  ese módulo en el servidor.
- Agente FF configurado para el emisor, ambiente, sucursal y punto correctos.
  Certificados, credenciales y alta del servicio se gestionan con FF.
- Una sola instalación emisora para ese perfil. Evitar carpetas compartidas
  o sincronizadas en nube para los archivos operativos.

Validar el flujo en un entorno de pruebas separado antes de habilitarlo en
producción. No trasladar un estado productivo a pruebas para emitir desde él.

## 2. Obtener los ejecutables

Si se dispone de un paquete de ejecutables verificado de esta versión, pasar
al apartado 3. El ZIP de GitHub contiene fuentes, por lo que puede compilarse
en Windows con Python compatible con la sintaxis 3.10+ y las dependencias.

Desde la carpeta extraída del repositorio:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    if (Test-Path -LiteralPath '.\config.ini') {
        throw 'Ya existe config.ini. Revise si se trata de una actualización.'
    }
    py -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno virtual.' }
    .\.venv\Scripts\python.exe -m pip install -r .\requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Falló la instalación de dependencias.' }
    Copy-Item -LiteralPath '.\config.example.ini' -Destination '.\config.ini'
    .\.venv\Scripts\python.exe .\compilar.py
    if ($LASTEXITCODE -ne 0) { throw 'Falló la compilación. No distribuir las salidas.' }
}
```

La plantilla permanece vacía durante la compilación. El paquete generado
incluye `Conector_V5.2.8.exe`, `SecuenciaFiscal.exe`, guías y una plantilla
de INI. No incluye un Agente ni un contador inicializado. `dist` es una salida
local de compilación y no debe subirse a GitHub.

Ver [Compilación en Windows](COMPILAR_WINDOWS.md) para detalles y pruebas.

## 3. Crear una carpeta operativa nueva

Si `C:\Conector` ya existe, comprobar si guarda una instalación anterior antes
de seguir. Para una ruta que realmente no existe:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $carpetaConector = 'C:\Conector'
    if (Test-Path -LiteralPath $carpetaConector) {
        throw 'La carpeta ya existe. No corresponde al procedimiento de instalación limpia.'
    }
    Expand-Archive -LiteralPath '.\dist\Conector_V5.2.8.zip' -DestinationPath $carpetaConector
}
```

El operador debe tener permisos en la ruta elegida. El paquete crea la
estructura de trabajo `Agente_FF` y `Agente_FF\Resultado_FF`. Configurar el
Agente para consumir y devolver resultados en esa instalación; no dejarlo
apuntando a otra carpeta.

## 4. Elegir el modo y obtener los datos de Odoo

Para una instalación nueva con contador local se requiere protocolo 2;
la base de compatibilidad para sucursales alfanuméricas es módulo 4.1.5.
Un módulo posterior debe conservar esa compatibilidad y validarse aparte.

En Odoo, con la compañía correcta seleccionada y sin sesiones o pedidos
pendientes de ese TPV, configurar el perfil FF y el modo previsto antes de
abrir el POS. Copiar la vinculación del TPV al INI de la PC. Un perfil ya
activado en local puede estar bloqueado para cambios; no forzarlo en SQL.

| Dato | De dónde obtenerlo |
|---|---|
| RUC emisor | Perfil de la empresa configurado en el Agente FF |
| Ambiente | El mismo configurado en FF: `1` producción, `2` pruebas |
| Sucursal / punto | Códigos registrados y habilitados en FF |
| `odoo_database` | Nombre exacto de la base, confirmado por el administrador |
| `odoo_company_id` | ID interno de `res.company` de esa compañía |
| `odoo_pos_config_id` | ID interno de `pos.config` de ese TPV |

La dirección del POS suele contener `config_id=...`; ese valor corresponde
al TPV. El parámetro `cids` identifica el contexto de compañía. Verificar ambos
contra la vinculación mostrada por el módulo; no usar el número `POS/...` ni
el ID de sesión como `odoo_pos_config_id`.

En modo heredado, conservar `modo = odoo` y no inicializar SQLite local.
Ver la [matriz de compatibilidad](MIGRACION.md).

## 5. Completar config.ini

Abrir `C:\Conector\config.ini` y completar sus secciones únicas. Los textos
en mayúsculas del siguiente bloque son campos por sustituir, no valores
válidos para iniciar:

```ini
[Target]
gaveta =
sucursal = CODIGO_FF_DE_4_CARACTERES
punto_de_facturacion = CODIGO_FF_DE_3_DIGITOS
Nombre_impresora =
modo_apertura_gaveta = efectivo

[FiscalSequence]
modo = local
emisor = RUC_EMISOR_CONFIRMADO
ambiente = AMBIENTE_CONFIRMADO
odoo_database = BASE_CONFIRMADA
odoo_company_id = ID_COMPANIA_CONFIRMADO
odoo_pos_config_id = ID_TPV_CONFIRMADO
```

- Sucursal: cuatro caracteres ASCII en mayúscula, `A-Z` o `0-9`.
- Punto: tres dígitos de `001` a `999`, conservando los ceros.
- Gaveta: puerto real, por ejemplo `COM3`, si se usa apertura serial.
- Impresora: nombre exacto de Windows, si esa función se utiliza.
- `efectivo` abre la gaveta cuando corresponde a pagos de efectivo;
  `siempre` conserva la política de apertura después de procesamiento válido.

El campo ambiente del conector no cambia el del Agente: ambos se configuran
por separado. No introducir PIN, certificado, contraseña o QR de acceso en
el repositorio.

## 6. Conciliar antes de inicializar el modo local

Con el conector detenido, confirmar para ese perfil el último número usado
por cada tipo de documento, incluidos los emitidos por otro ERP/PAC si
comparte la identidad. Revisar también documentos pendientes o con resultado
incierto y las colas del Agente; el máximo autorizado visible puede no incluir
reservas o envíos aún pendientes.

El contador del conector es común a facturas y devoluciones de su flujo.
FF mantiene información por tipos; validar que la continuidad elegida sea
compatible antes de habilitar la emisión. No tomar un máximo de otro punto,
compañía o ambiente.

Desde PowerShell, situado en la carpeta operativa:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\Conector'
    .\SecuenciaFiscal.exe init --last-invoice ULTIMA_FACTURA_CONFIRMADA --last-credit ULTIMO_NUMERO_NC_DEVOLUCIONES_CONFIRMADO --confirm-ff-and-queues
    if ($LASTEXITCODE -ne 0) { throw 'Falló la inicialización. No continuar.' }
    .\SecuenciaFiscal.exe status
    if ($LASTEXITCODE -ne 0) { throw 'Falló la consulta del estado.' }
}
```

Sustituir los dos marcadores por enteros conciliados. Los parámetros son los
**últimos números utilizados**, no los siguientes. El próximo número será el
mayor de ambos más uno, dentro del rango permitido.

Si nunca hubo notas de crédito o devoluciones, `--last-credit 0` corresponde
solo después de confirmarlo con FF y las colas. `0/0` corresponde únicamente
a un perfil que nunca emitió ni tiene reservas. No aplicar esos valores por
ser una PC nueva. La utilidad no tiene opción separada para notas de débito;
si existen otros tipos o sistemas emisores, resolver el corte con FF antes
de continuar.

La confirmación `--confirm-ff-and-queues` registra la decisión del operador;
no consulta por sí sola el portal de FF ni prueba que la conciliación se hizo.
Si ya existe un SQLite o una marca de activación, usar la guía de recuperación.

## 7. Arranque y comprobación

1. Iniciar el Agente y comprobar su carpeta, emisor, ambiente y punto.
2. Ejecutar `Conector_V5.2.8.exe` una sola vez desde la carpeta operativa.
3. Abrir el POS en esa misma PC y verificar la compañía y el TPV correctos.
4. Confirmar versión e inicio sin errores en el log y conexión local:

```powershell
Test-NetConnection -ComputerName 127.0.0.1 -Port 3222
Get-Content -LiteralPath '.\log.txt' -Tail 60
```

El puerto abierto no prueba autorización fiscal. Validar el flujo completo
en pruebas: respuesta del Agente, documento correcto, consulta en FF y pedido
registrado en Odoo, con el mismo número y CUFE. En producción, verificar las
operaciones reales autorizadas; no crear ventas ficticias para probar.

Crear después el acceso de inicio automático apuntando a esa carpeta y a la
versión correcta. Evitar dos accesos que arranquen versiones distintas.

## 8. Operación diaria

Conservar y respaldar el estado; no repetir `init` al encender la PC. Revisar
pendientes del POS, del Agente y del portal y conciliar al cierre. Antes de
recargar o cambiar de perfil de navegador, proteger sus pedidos pendientes.
Una copia del VPS no sustituye esa copia local.

Consultar [Secuencias](SECUENCIAS_Y_CAMBIO_PUNTO.md) antes de alcanzar un límite
y [Solución de problemas](SOLUCION_DE_PROBLEMAS.md) si aparece un documento
pendiente, un rechazo o una diferencia con Odoo.
