@echo off
chcp 65001 >nul
title ViralCuts

echo Iniciando ViralCuts...
cd /d "%~dp0"

:: Check node_modules
if not exist "node_modules\" (
    echo node_modules nao encontrado. Executando setup...
    call setup.bat
)

:: Start Electron (which will spawn Python backend automatically)
npm start
