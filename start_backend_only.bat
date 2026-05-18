@echo off
chcp 65001 >nul
title ViralCuts Backend
echo Iniciando backend Python na porta 5050...
cd /d "%~dp0"
python backend\server.py
pause
