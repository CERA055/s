@echo off
rem hourly claim through a real browser (keep this file ASCII-only: cmd reads .bat as GBK)
rem chcp 65001 keeps python's UTF-8 stdout consistent when redirecting to the log
chcp 65001 >nul
cd /d "%~dp0"
"C:\Python314\python.exe" gui_claim.py --kind hourly --wait 30 >> gui.log 2>&1
