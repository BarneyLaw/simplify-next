#!/usr/bin/env sh
set -eu

export ADAPTSG_MODE=live
export ADAPTSG_USE_BEDROCK=false

exec streamlit run streamlit_app.py
