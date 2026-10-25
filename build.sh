#!/usr/bin/env bash
set -euo pipefail
python -m pip install -r requirements.txt
# Playwright's Python package does not install the browser executable itself.
python -m playwright install chromium
python -m playwright install --dry-run chromium
