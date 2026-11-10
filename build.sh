#!/usr/bin/env bash
set -euo pipefail
# OCR para recuperação de cartões com QR danificado. Não esconder falhas
# de instalação: sem Tesseract, a importação alternativa não funciona.
if ! command -v tesseract >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update && apt-get install -y --no-install-recommends tesseract-ocr
  else
    echo "ERRO: Tesseract ausente e apt-get indisponível neste ambiente" >&2
    exit 1
  fi
fi
command -v tesseract
python -m pip install -r requirements.txt

# O exportador de PDFs da pré-impressão precisa do Chromium no Render.
python -m playwright install chromium
