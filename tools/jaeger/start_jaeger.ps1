$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "Starting Jaeger v2.23.0..." -ForegroundColor Cyan
Write-Host " - OTLP HTTP receiver: http://localhost:4318/v1/traces" -ForegroundColor Green
Write-Host " - Jaeger Web UI:      http://localhost:16686" -ForegroundColor Yellow
Write-Host ""

& "$ScriptDir\jaeger-2.23.0-windows-amd64\jaeger.exe" --config="$ScriptDir\config.yaml"

