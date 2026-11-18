# Запуск бота на этом компьютере одной командой:  .\start.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Создаю виртуальное окружение..." -ForegroundColor Cyan
    python -m venv .venv
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "Устанавливаю зависимости..." -ForegroundColor Cyan
# --trusted-host нужен, если антивирус или прокси подменяет SSL-сертификаты.
& $python -m pip install --quiet --disable-pip-version-check `
    --trusted-host pypi.org --trusted-host files.pythonhosted.org `
    -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "Создан файл .env." -ForegroundColor Yellow
    Write-Host "Вставьте токен от @BotFather в строку BOT_TOKEN= и сохраните файл." -ForegroundColor Yellow
    Write-Host "Сейчас откроется Блокнот..." -ForegroundColor Yellow
    Start-Process notepad.exe -ArgumentList ".env" -Wait
}

Write-Host "Запускаю бота. Остановить — Ctrl+C." -ForegroundColor Green
& $python bot.py
