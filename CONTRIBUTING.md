# Contributing

## Quick Run (Windows PowerShell)

```powershell
python -m venv venv; venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # fill your own keys, never commit .env
flask db upgrade
python app.py                 # http://localhost:5000
```

## Branches & PRs

- Branch from `main`: `feat/<short-name>` or `fix/<short-name>`.
- One change per PR, under ~500 lines. Conventional titles, e.g. `fix(api): handle empty Gmail batch`.
- PR body: what / why / how tested. Link issues with `Closes #N`.
- CI must be green (compile + migration smoke). Self-review before requesting review.

## Never Commit

`.env`, `credentials.json`, `token.pickle`, `emails.db`, `*.pth`, `*.joblib`,
`*.pdf`, `*.docx`, `diagrams/`, `IEEE Papers/`, `audit/`, `fix_*.py`, `chk*.py`,
`build_*.py`, `content_part*.py`, `debug_*.py`, `__pycache__/`, `flask_session/`.

## Screenshots

Blur real names/addresses. Keep each under ~500 KB in `docs/screenshots/`.
