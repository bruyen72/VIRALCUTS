@echo off
chcp 65001 >nul
title ViralCuts — Instalação

echo.
echo ██╗   ██╗██╗██████╗  █████╗ ██╗      ██████╗██╗   ██╗████████╗███████╗
echo ██║   ██║██║██╔══██╗██╔══██╗██║     ██╔════╝██║   ██║╚══██╔══╝██╔════╝
echo ██║   ██║██║██████╔╝███████║██║     ██║     ██║   ██║   ██║   ███████╗
echo ╚██╗ ██╔╝██║██╔══██╗██╔══██║██║     ██║     ██║   ██║   ██║   ╚════██║
echo  ╚████╔╝ ██║██║  ██║██║  ██║███████╗╚██████╗╚██████╔╝   ██║   ███████║
echo   ╚═══╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═════╝    ╚═╝   ╚══════╝
echo.
echo ViralCuts — AI Shorts Generator
echo Configurando ambiente...
echo.

:: Python deps
echo [1/3] Instalando dependencias Python...
pip install -r backend\requirements.txt --quiet --disable-pip-version-check
if %errorlevel% neq 0 (
    echo ERRO: Falha ao instalar dependencias Python.
    pause
    exit /b 1
)
echo     OK

:: Node deps
echo [2/3] Instalando Electron e dependencias Node...
npm install --silent
if %errorlevel% neq 0 (
    echo ERRO: Falha ao instalar dependencias Node.
    pause
    exit /b 1
)
echo     OK

echo [3/3] Instalacao concluida!
echo.
echo Para iniciar o app, execute:  start.bat
echo.
pause
