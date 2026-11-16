[CmdletBinding()]
param(
    [string]$DirectorioConector = (Join-Path $env:USERPROFILE 'Desktop\Conector'),
    [ValidateRange(30, 1800)]
    [int]$DuracionSegundos = 300,
    [string]$DirectorioSalida = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$rutaConector = (Resolve-Path -LiteralPath $DirectorioConector).Path
$rutaAgente = Join-Path $rutaConector 'Agente_FF'
if (-not (Test-Path -LiteralPath $rutaAgente -PathType Container)) {
    throw "No existe la carpeta del Agente FF: $rutaAgente"
}

$rutaSalida = [IO.Path]::GetFullPath($DirectorioSalida)
if (-not (Test-Path -LiteralPath $rutaSalida -PathType Container)) {
    throw "No existe el directorio de salida: $rutaSalida"
}

$marca = Get-Date -Format 'yyyyMMdd-HHmmss'
$csv = Join-Path $rutaSalida "latencia-ff-$marca.csv"
$log = Join-Path $rutaSalida "latencia-ff-$marca-conector.log"
$registros = [Collections.Generic.List[object]]::new()
$cronometro = [Diagnostics.Stopwatch]::StartNew()
$prefijoEventos = "HKS.Latencia.$([Guid]::NewGuid().ToString('N'))"

$watcher = [IO.FileSystemWatcher]::new($rutaAgente)
$watcher.IncludeSubdirectories = $true
$watcher.Filter = '*'
$watcher.NotifyFilter = [IO.NotifyFilters]'FileName, LastWrite, Size, CreationTime'

$suscriptores = @(
    Register-ObjectEvent -InputObject $watcher -EventName Created -SourceIdentifier "$prefijoEventos.Created"
    Register-ObjectEvent -InputObject $watcher -EventName Changed -SourceIdentifier "$prefijoEventos.Changed"
    Register-ObjectEvent -InputObject $watcher -EventName Deleted -SourceIdentifier "$prefijoEventos.Deleted"
    Register-ObjectEvent -InputObject $watcher -EventName Renamed -SourceIdentifier "$prefijoEventos.Renamed"
)
$identificadores = @($suscriptores | Select-Object -ExpandProperty SourceIdentifier)
$watcher.EnableRaisingEvents = $true

function Recibir-EventosFF {
    foreach ($identificador in $identificadores) {
        foreach ($evento in @(Get-Event -SourceIdentifier $identificador -ErrorAction SilentlyContinue)) {
            $argsEvento = $evento.SourceEventArgs
            $nombreRelativo = [string]$argsEvento.Name
            $nombreArchivo = [IO.Path]::GetFileName($nombreRelativo)
            $esFiscal = (
                $nombreArchivo -match '^\d{10}\.txt$' -or
                $nombreArchivo -match '^result_\d{10}_\d+(?:\.txt)?$' -or
                $nombreArchivo -match '^\.fiscal-.*\.pending$'
            )
            if ($esFiscal) {
                $tamano = $null
                try {
                    $elemento = Get-Item -LiteralPath $argsEvento.FullPath -ErrorAction Stop
                    if (-not $elemento.PSIsContainer) {
                        $tamano = $elemento.Length
                    }
                } catch {
                    # Un movimiento o borrado puede ocurrir antes de consultar el tamaño.
                }
                $nombreAnterior = if ($argsEvento -is [IO.RenamedEventArgs]) {
                    [string]$argsEvento.OldName
                } else {
                    ''
                }
                $registros.Add([pscustomobject]@{
                    fecha_utc = $evento.TimeGenerated.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
                    transcurrido_ms = [math]::Round($cronometro.Elapsed.TotalMilliseconds, 1)
                    evento = [string]$argsEvento.ChangeType
                    ruta = $nombreRelativo
                    ruta_anterior = $nombreAnterior
                    bytes = $tamano
                })
            }
            Remove-Event -EventIdentifier $evento.EventIdentifier -ErrorAction SilentlyContinue
        }
    }
}

try {
    Write-Host "Midiendo solo eventos de archivos fiscales durante $DuracionSegundos segundos."
    Write-Host 'Realice una venta después de una pausa y luego dos ventas consecutivas.'
    Write-Host 'No cierre el conector ni el Agente FF durante la medición.'
    while ($cronometro.Elapsed.TotalSeconds -lt $DuracionSegundos) {
        Start-Sleep -Milliseconds 200
        Recibir-EventosFF
    }
    Recibir-EventosFF
} finally {
    $watcher.EnableRaisingEvents = $false
    foreach ($suscriptor in $suscriptores) {
        Unregister-Event -SubscriptionId $suscriptor.SubscriptionId -ErrorAction SilentlyContinue
    }
    foreach ($identificador in $identificadores) {
        Remove-Event -SourceIdentifier $identificador -ErrorAction SilentlyContinue
    }
    $watcher.Dispose()
}

if ($registros.Count -gt 0) {
    $registros | Sort-Object fecha_utc, transcurrido_ms | Export-Csv -LiteralPath $csv -NoTypeInformation -Encoding UTF8
} else {
    'No se detectaron archivos fiscales durante la ventana de medición.' | Set-Content -LiteralPath $csv -Encoding UTF8
}

$rutaLogConector = Join-Path $rutaConector 'log.txt'
if (Test-Path -LiteralPath $rutaLogConector -PathType Leaf) {
    Get-Content -LiteralPath $rutaLogConector -Tail 1000 | Set-Content -LiteralPath $log -Encoding UTF8
}

Write-Host "Medición terminada: $csv"
if (Test-Path -LiteralPath $log -PathType Leaf) {
    Write-Host "Log del conector: $log"
}

