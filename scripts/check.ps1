$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest
python -m bandit -q -r src api streamlit_app.py
python -m pip_audit --skip-editable
cfn-lint infra/aws/template.yaml infra/aws/bootstrap.yaml
python -m json.tool vercel.json | Out-Null

Write-Output "All AdaptSG correctness gates passed."
