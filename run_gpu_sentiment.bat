@echo off
REM ============================================================
REM  GPU FinBERT Sentiment Backfill — RTX 3070
REM  Run this from your regular CMD or PowerShell as:
REM      cd C:\cpio_db\CNFR-real
REM      run_gpu_sentiment.bat
REM ============================================================

echo [1/4] Installing PyTorch (CUDA 12.6 for Python 3.13)...
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126

echo [2/4] Installing NLP + DB dependencies...
pip install transformers sentencepiece psycopg2-binary python-dotenv

echo [3/4] Verifying GPU...
python -c "import torch; print('CUDA:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU')"

echo [4/4] Running FinBERT backfill (GPU, batch=256)...
python -m src.nlp.sentiment --batch-size 256 --from-date 2025-10-21

echo.
echo Done! Now run news_signals aggregation:
echo   python -m src.features.news_signals --from-date 2025-10-21
pause
