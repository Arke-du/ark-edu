#!/usr/bin/env bash
set -euo pipefail
python -m pip install -r requirements.txt

# O exportador de PDFs da pré-impressão precisa do Chromium no Render.
python -m playwright install chromium
