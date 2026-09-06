# AI-Powered Email Spam Detection and Threat Analysis

![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)
![Flask 2.3](https://img.shields.io/badge/Flask-2.3-black.svg)

A local-first web app that reads your Gmail (read-only), classifies each
message into one of six categories with a fine-tuned RoBERTa + sklearn
ensemble, and explains every verdict with a risk breakdown, urgency score,
and sender history. Built as a 7th-semester major project by
**Abhishek Kumar T**. No hosting, no data leaves your machine.

## What It Looks Like

| Landing | Dashboard |
|---|---|
| ![Landing page](docs/screenshots/landing.png) | ![Dashboard](docs/screenshots/dashboard.png) |

| Results | Email view |
|---|---|
| ![Results table](docs/screenshots/results.png) | ![Email view with analysis](docs/screenshots/email-view.png) |

## How One Scan Works

```text
Login with Google → Dashboard → Scan inbox
  → Gmail API fetches latest 50 (batched, with retry)
  → 0.90 × RoBERTa + 0.10 × Ensemble votes per message
  → risk + urgency + sender reputation layered on top
  → Results table → open any mail for the full breakdown
```

Six categories: `legitimate, spam, promotion, phishing, malware, newsletter`.
Only spam, phishing, and malware count as threats — promotions and
newsletters never do, even when they read like marketing.

## Run It Locally

```powershell
python -m venv venv; venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # fill in your own keys (Section: Gmail keys)
flask db upgrade
python app.py                 # http://localhost:5000
```

Single worker, SQLite file `emails.db`, torch CPU. A 50-mail scan takes
roughly a minute on a laptop.

## Gmail Keys (Everyone Brings Their Own)

1. Google Cloud → new project → enable the **Gmail API**.
2. OAuth consent screen (External, Test mode is enough for a demo).
3. OAuth Client ID (Web app) → redirect URI
   `http://localhost:5000/callback/google`.
4. Paste the ID and secret into `.env`.

The app requests **readonly** access. It cannot send, delete, or relabel
anything. Revoke anytime from Google Account → Security. Never commit
`.env`, `credentials.json`, or `emails.db` — see `SECURITY.md`.

## Models

Running needs four files from **Releases → `v1.0-models`**, placed in `models/`:

| File | Size | Role |
|---|---|---|
| `best_roberta_model.pth` | ~476 MB | Fine-tuned `roberta-base`, 6 labels, max_len 256 |
| `ensemble_model.joblib` | ~192 MB | 5-head soft-voting sklearn ensemble |
| `vectorizer.joblib` | ~1.3 MB | 20k TF-IDF (1–2gram) vocabulary |
| `encoder.joblib` | tiny | Label map |

The repo and zip ship code only. If the files are missing,
`/api/analyze_text` answers **503** instead of guessing.

Retrain from scratch: `python models/roberta_train.py` →
`python models/ensemble_train.py` →
`python models/evaluate.py --model both` (scores on the frozen
`test_set.csv`, which is eval-only and never trained on).

## Dataset

`models/final_training_dataset.csv` — **30,028 rows** merged from public
spam/phishing collections plus author-written synthetic mails, labelled
into 6 classes (spam 6457, legitimate 5532, promotion 5208, phishing 4837,
newsletter 4502, malware 3492). Columns: `text,label,category`.

## Pages

Dashboard, Results (filter + sort + 50/page), Email view (sanitized body +
risk breakdown + AI summary), Analytics, Threat console, Bulk analyze
(paste up to 100 texts), plus `/api/analyze_text`, `/api/status`,
`/health`. See `CONTRIBUTING.md` for the branch/PR rules.

## Honest Limitations

CPU inference runs ~200–500 ms per mail. Trend windows are 7 days.
Urgency heuristics over-fire on `noreply@` senders. Multi-worker deploy
is unsupported (rate limiter and task store are in-process).

## License

MIT — see `LICENSE`. Copyright (c) 2026 Abhishek Kumar T.
