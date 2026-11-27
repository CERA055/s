@echo off
rem local hourly fallback. Keep this file ASCII-only: cmd.exe reads .bat as GBK.
rem Do NOT redirect here - soliumbra_claim.py writes claim.log itself in UTF-8,
rem and mixing cmd's ANSI redirect with python's UTF-8 output garbles the file.
cd /d "%~dp0"
"C:\Python314\python.exe" soliumbra_claim.py --all --notify --quiet-ok
