@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\CodeX\PaperDistill\pdf-parser-benchmark\scripts\sync_corpus10000.ps1
exit /b %errorlevel%
