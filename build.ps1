$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
py -m pip install -r .\requirements-build.txt
py .\compilar.py
