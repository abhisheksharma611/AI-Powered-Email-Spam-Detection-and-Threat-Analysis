# AI-Powered Email Spam Detection and Threat Analysis

![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.3-black.svg)
![RoBERTa](https://img.shields.io/badge/RoBERTa-6--class-orange.svg)

> 7th-sem major project by **Abhishek Kumar T** (fully owned pre-submission).
> Local-only Gmail threat triage. No hosting needed — clone and run on `http://localhost:5000`.

## Screenshots

| Landing | Dashboard |
|---|---|
| ![Landing](docs/screenshots/landing.png) | ![Dashboard](docs/screenshots/dashboard.png) |

| Results | Email view |
|---|---|
| ![Results](docs/screenshots/results.png) | ![Email view](docs/screenshots/email-view.png) |

## 1. What This Repo Contains

`app.py`, `config.py`, `models/` (predictor, RoBERTa, ensemble, training scripts,
`final_training_dataset.csv`, `test_set.csv`), `utils/` (auth, Gmail, helpers),
`templates/`, `static/`, `migrations/`, `requirements.txt`.
**Not in git:** `emails.db`, `.env`, `*.pth` / `*.joblib` (see Models).

## 2. Secrets — Read Before Running

Never commit `.env`, `credentials.json`, `token.pickle`, or `emails.db`.
Copy the example and fill your own keys (nobody else's keys work for you):

```powershell
Copy-Item .env.example .env
```

Do not post tokens or mailbox dumps in issues (see `SECURITY.md`).

## 3. License

MIT — see `LICENSE`. Copyright (c) 2026 Abhishek Kumar T.

## 4. Gmail Setup (Each User Brings Own Keys)

1. Google Cloud → new project → enable **Gmail API**.
2. OAuth consent screen (External, Test mode is fine for demo).
3. Create OAuth Client ID (Web) → redirect URI `http://localhost:5000/callback/google`.
4. Paste client ID/secret into `.env`. Scope used is **readonly**; app never sends or deletes mail.

## Quickstart

```powershell
python -m venv venv; venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # fill keys
flask db upgrade
python app.py                 # http://localhost:5000
```

Login with Google → Dashboard → Scan inbox (10 mails) → Results → open an
email → Analytics / Threat console → Bulk analyze for pasted texts.

## Models

- Classes (6): `legitimate, spam, promotion, phishing, malware, newsletter`.
  `is_spam` = spam/phishing/malware only — promotion/newsletter are never spam.
- Serve blend: `0.90 × RoBERTa + 0.10 × Ensemble`. Missing artifacts →
  `/api/analyze_text` returns **503**, never a fake label.
- Running needs (from Releases `v1.0-models`, into `models/`):
  `best_roberta_model.pth` (~476 MB), `ensemble_model.joblib` (~192 MB),
  `vectorizer.joblib`, `encoder.joblib`. Repo/zip ships code only.
- Train: `python models/roberta_train.py` → `python models/ensemble_train.py` →
  `python models/evaluate.py --model both` (frozen `test_set.csv`, eval only).

## Dataset

`models/final_training_dataset.csv` — **30,028 rows** curated merge plus
author-written synthetic augmentation (spam 6457, legitimate 5532, promotion
5208, phishing 4837, newsletter 4502, malware 3492). Columns
`text,label,category`. `models/test_set.csv` is the frozen holdout.

## Limitations

Single worker, SQLite, torch CPU (~200–500 ms/mail), 50-mail scans,
localhost OAuth only, no auto-delete/label writes.
