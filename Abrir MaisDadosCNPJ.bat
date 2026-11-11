@echo off
cd /d "%~dp0"
start "Mais Dados CNPJ" /min cmd /c "uv run python -X utf8 interface.py || pause"
