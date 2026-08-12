@echo off
setlocal
cd /d C:\Users\zzx\PaperDistillGPU\benchmark-v1
envs\eval\Scripts\python.exe scripts\run_mineru_corpus_service.py --config config\benchmark.json --run-label corpus10000-prod --primary-backend pipeline --skip-any-success --client-concurrency 8 --api-concurrency 8 >> logs\corpus10000-prod.stdout.log 2>> logs\corpus10000-prod.stderr.log
exit /b %errorlevel%
