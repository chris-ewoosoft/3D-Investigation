@echo off
cd /d "%~dp0"
echo Starting Jaeger v2.23.0...
echo - OTLP HTTP receiver: http://localhost:4318/v1/traces
echo - Jaeger Web UI:      http://localhost:16686
echo.
jaeger-2.23.0-windows-amd64\jaeger.exe --config=config.yaml
pause

