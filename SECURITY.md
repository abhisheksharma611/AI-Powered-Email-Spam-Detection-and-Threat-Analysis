# Security Policy

## Supported Versions

Latest `main` only. This is a college major project; security fixes land on `main` without backports.

## What This App Touches

- Gmail **read-only** (`gmail.readonly`). No send, delete, or label writes.
- Google OAuth tokens stay **local** (server-side session + encrypted store). They are never committed.
- SQLite `emails.db` may contain real subjects/senders. Never attach it to issues.

## Do Not Post in Issues

`.env`, `credentials.json`, `token.pickle`, access/refresh tokens, `emails.db`, or full email bodies with personal data. If you posted one by accident, tell us immediately so we can rotate keys.

## Reporting a Vulnerability

Email the maintainer (see GitHub profile) with steps to reproduce. Expect a first response within 7 days. Do not open a public issue for unpatched secrets or auth bypasses.

## Revoking Access

Google Account → Security → Third-party access → remove the app. Then delete local `emails.db` and `flask_session/` if you want a clean slate.
