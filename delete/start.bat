@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem setup.bat で作成した仮想環境があればそれを使い、無ければシステムの python を使う
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else (
    python main.py
)

if errorlevel 1 (
    echo.
    echo [エラー] ゲームの起動中にエラーが発生しました。
    echo 先に setup.bat を実行してセットアップを完了してください。
    pause
)
endlocal
