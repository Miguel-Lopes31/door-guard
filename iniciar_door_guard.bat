@echo off
setlocal

echo ============================================
echo   Door Guard
echo ============================================
echo.

REM --- Passo 1: confirmar que o Python esta instalado ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao foi encontrado no seu computador.
    echo.
    echo Baixe e instale em: https://www.python.org/downloads/
    echo IMPORTANTE: marque a opcao "Add python.exe to PATH" durante a instalacao.
    echo Depois de instalar, feche esta janela e rode este arquivo de novo.
    echo.
    pause
    exit /b 1
)

REM --- Passo 2: criar um ambiente isolado so para este programa (uma vez) ---
if not exist venv (
    echo [1/3] Primeira execucao detectada. Preparando ambiente isolado...
    python -m venv venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o ambiente virtual.
        pause
        exit /b 1
    )
    echo [OK] Ambiente criado.
) else (
    echo [1/3] Ambiente ja preparado, pulando essa etapa.
)

REM --- Passo 3: instalar/atualizar as dependencias (rapido se ja instaladas) ---
echo [2/3] Verificando dependencias...
venv\Scripts\python.exe -m pip install --quiet --upgrade pip
if not exist requirements.txt (
    echo [ERRO] Arquivo requirements.txt nao encontrado nesta pasta.
    echo Coloque requirements.txt na mesma pasta que este arquivo .bat.
    pause
    exit /b 1
)
venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar as dependencias. Verifique sua conexao com a internet.
    pause
    exit /b 1
)
echo [OK] Dependencias prontas.

REM --- Passo 4: rodar o app ---
echo [3/3] Iniciando o Door Guard...
echo.
if not exist door_guard.py (
    echo [ERRO] Arquivo door_guard.py nao encontrado nesta pasta.
    pause
    exit /b 1
)
venv\Scripts\python.exe door_guard.py

endlocal