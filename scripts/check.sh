#!/usr/bin/env sh
set -eu

python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest
python -m bandit -q -r src api streamlit_app.py
python -m pip_audit --skip-editable
node scripts/check_web.mjs
python -m json.tool vercel.json >/dev/null

echo "All AdaptSG correctness gates passed."

