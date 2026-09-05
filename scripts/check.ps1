$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

python -m ruff format --check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m mypy
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m bandit -q -r src api streamlit_app.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m pip_audit --skip-editable
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cfn-lint infra/aws/template.yaml infra/aws/bootstrap.yaml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m json.tool vercel.json | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "All AdaptSG correctness gates passed."
