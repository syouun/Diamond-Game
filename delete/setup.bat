@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo  ダイヤモンドゲーム 環境セットアップ
echo ============================================
echo.

rem --- Python の確認 (python → py ランチャーの順に探す) ---
set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    py -3 --version >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    echo [エラー] Python が見つかりません。
    echo https://www.python.org/ から Python 3 をインストールし、
    echo インストール時に「Add python.exe to PATH」にチェックを入れてください。
    echo.
    pause
    exit /b 1
)
echo Python を検出しました:
%PY% --version
echo.

rem --- 仮想環境の作成 ---
if not exist ".venv\Scripts\python.exe" (
    echo 仮想環境 .venv を作成しています...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [エラー] 仮想環境の作成に失敗しました。
        pause
        exit /b 1
    )
) else (
    echo 既存の仮想環境 .venv を使用します。
)
echo.

rem --- 依存ライブラリのインストール ---
echo 依存ライブラリをインストールしています...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [エラー] ライブラリのインストールに失敗しました。
    echo ネットワーク接続を確認して、もう一度 setup.bat を実行してください。
    pause
    exit /b 1
)

echo.
echo セットアップが完了しました。start.batを実行してください。
echo.
pause
endlocal
