# Laya-only Test Text Modal — Design

**Date:** 2026-10-09
**Status:** Draft for review
**Scope:** Experiment only. Laya replaces RoBERTa + ensemble on the Test Text modal, and nowhere else.

## Goal

The dashboard's "Test Text" button and its modal should classify pasted text using
Laya (`laya:en` via Ollaya on port 11435) instead of the RoBERTa + ensemble stack.

This is a **local experiment** to see how Laya behaves on this corpus. It is not a
production swap. The Start Analysis path, the inbox scan, the results pages and the
database keep using RoBERTa + ensemble unchanged.

## Non-goals

- Do not delete `models/predictor.py`, `models/roberta_model.py` or any artefact.
- Do not change the 0.90 / 0.10 fusion, the risk scoring, or `BASE_RISKS`.
- Do not add a database column or a migration.
- Do not add Laya to `/api/start_analysis`, `/bulk_analyze` or any background scan.
- Do not change `requirements.txt` — `requests` is already pinned at line 14.

## Verified facts this design depends on

These were measured live against `localhost:11435` on 2026-10-09, not assumed.

| Fact | Value |
|---|---|
| Endpoint | `POST /v1/systemone` |
| Server root | `Ollaya is running` (HTTP 200) |
| Models present | `laya:en` (421M), `laya:multilingual` (322M), `laya:latest` (router) |
| `questions` | must be a **dict**. A list returns HTTP 422. |
| `criteria` | must be a **list of plain strings**. Dicts return HTTP 422. |
| `noul` answer key | value lives under `"noul"`, **not** `"score"` |
| `choice` answer | returns `choice`, `confidence`, `probabilities` (full vector) |
| Output tokens | 0 — it scores, it does not generate |
| Batching | **not supported.** 10 emails in one `state` returns HTTP 422. |
| Latency | ~50 ms warm, 7.4 s on the first call after server start |
| `usage.output_tokens` | 0 |

### Measured accuracy, 60 held-out rows from `models/test_set.csv`

| System | Accuracy |
|---|---|
| RoBERTa + ensemble (current) | 96.7% (58/60) |
| Laya, single 6-way `choice` | 50.0% (30/60) |
| Laya, five binary `noul` questions | 41.7% (25/60) |

Laya per-class recall on the binary variant: `legitimate 9/10`, `phishing 8/10`,
`malware 4/10`, `newsletter 2/10`, `promotion 2/10`, `spam 0/10`.

Laya is a zero-shot decision model and this corpus is not its training data, so a
large accuracy gap is expected. This is acceptable **because the scope is a
labelled experiment**. The UI must not present Laya as authoritative.

## Architecture

```
Browser (dashboard.html)
  "Test Text" button -> #textAnalysisModal  (unchanged)
  textarea #analysisText                    (unchanged)
  submit -> handleTextAnalysis               (modified: URL + label)
      |
      | POST /api/laya_text
      v
app.py  analyze_text_laya()                 (new route, ~35 lines)
      |
      | letter_count guard (MIN_LETTERS_TO_JUDGE)
      v
utils/laya_client.py  analyze_email()       (new file)
      |
      | POST http://localhost:11435/v1/systemone
      v
Ollaya -> laya:en
      |
      | {category, category_probabilities, category_confidence, latency_ms}
      v
displayAnalysisResultsDesktop(data)         (unchanged, reused as-is)
```

The results renderer is reused unchanged. Laya is shaped to match the payload the
renderer already expects, rather than the renderer being forked.

## Components

### 1. `utils/laya_client.py` (new)

Mirrors the structure of `utils/ai_explanation.py`: module-level `load_dotenv()`, a
`logger`, env-var constants with defaults, then functions that return `None` on any
failure so the caller degrades rather than crashes.

```python
LAYA_BASE_URL = os.environ.get('LAYA_BASE_URL', 'http://localhost:11435').rstrip('/')
LAYA_MODEL    = os.environ.get('LAYA_MODEL', 'laya:en')
LAYA_TIMEOUT  = int(os.environ.get('LAYA_TIMEOUT', '60'))
LAYA_KEEP_ALIVE = os.environ.get('LAYA_KEEP_ALIVE', '5m')
LAYA_MAX_CHARS = int(os.environ.get('LAYA_MAX_CHARS', '1400'))
```

Laya shares a 512-token budget between state and questions, so long pastes are
truncated. `_clip()` hard-truncates to `LAYA_MAX_CHARS`.

The six criteria use the `v2_disjoint` wording, which was the best of the three
phrasings tested:

```
Spam: unsolicited bulk junk that only wants attention
Malware: message carrying a harmful file, attachment or payload
Promotion: a marketing ad or sale from a company, no threat involved
Newsletter: a recurring digest or mailing-list issue
Phishing: a fake login, verification or payment page asking for secrets
Legitimate: genuine one-to-one or work mail from a real sender
```

Public surface:

| Function | Returns |
|---|---|
| `available()` | `bool` — `GET` on the server root |
| `analyze_email(subject, body, sender)` | result dict, or `None` on any failure |
| `warm_up()` | `bool` — pays the ~7 s cold load at app start |

`warm_up()` is **not** wired into app startup in this experiment. Calling it would add
a blocking ~7 s to boot, and Flask's dev reloader would pay it on every reload. The
first Laya click pays the cold load instead, inside the existing 25 s fetch timeout.
`warm_up()` exists so it can be called manually from a shell while testing.

### 2. `app.py` — `POST /api/laya_text` (new)

Placed directly after `analyze_text()` (which ends at line 2003). Decorated with
`@login_required` and `@rate_limit(max_req=10, window=60)`, matching the existing
endpoint.

Request: `{"text": "..."}`.
Response on success: the same keys `displayAnalysisResultsDesktop` reads —
`judgeable`, `letter_count`, `word_count`, `category`, `category_info`,
`is_spam`, `risk_score`, `risk_level`, `confidence`, `probabilities`,
`spam_probability`, `analysis_time`.

Response on Laya unavailable or failed: HTTP 503 with a plain message naming the
port. **No fallback to RoBERTa.** Falling back would mean the card says Laya and the
answer came from RoBERTa, which is a lie in the UI.

Risk and category metadata come from the existing `predictor.get_category_info()`
and `BASE_RISKS`. Those are display lookups, not model inference — reusing them keeps
one source of truth for icons, colours and descriptions and touches no artefact.

The `MIN_LETTERS_TO_JUDGE` guard (app.py:1958) is **kept**. On symbols the model
returns malware at 93% confidence, which is the exact failure this guard exists to
prevent. Verified against the live inbox: 0 of 50 real emails fall at or below 10
letters, so it cannot affect real traffic.

### 3. `templates/dashboard.html`

- A second submit button `#layaBtn` beside `#analyzeBtn` (around line 360), styled
  `btn-outline-success` to mark it as the experimental path.
- `handleTextAnalysis` reads which button was pressed and picks the URL. One shared
  textarea, one shared renderer, two endpoints.
- The results header states which engine produced the verdict.
- `resetTextAnalysisModal` clears the new button's state too.
- No health probe on page load. The button is never disabled, because a greyed-out
  button with no explanation is worse than a click that returns a clear message. The
  server-down case surfaces as a 503 in the modal instead.

The modal already has a two-column layout, `col-md-5` for input and `col-md-7` for
results, so the extra button fits without a layout change.

## State coverage

| State | Behaviour |
|---|---|
| First click after server start | Loading spinner for ~7 s. The existing 25 s fetch timeout (line 774) and 30 s backup timer (line 763) both cover this. |
| Ollaya not running | HTTP 503, modal shows "Laya server not reachable on port 11435" |
| Laya returns a bad payload | Same 503. Logged at ERROR with the payload. |
| Text too short | `judgeable: false`, reuses the existing `displayUnjudgeable` path. |
| Warm request | ~50 ms, feels instant. |

## Testing

- `available()` returns `True` against the running server, `False` when it is down.
- A known phishing paste returns `category == 'phishing'`.
- A known newsletter paste returns `category == 'newsletter'`.
- Server down → route returns 503, no exception escapes.
- `$%^&*()` → `judgeable` is `false`, no category rendered.
- The existing `Phase 3 Report/tests/test_web.py` cases for `/api/analyze_text`
  still pass — that route is not modified.

## Known limitations, stated plainly

1. **Accuracy is 50%**, against 96.7% for the stack it is standing in for. Fine for
   an experiment, not for shipping.
2. **No batching.** One email per request. This is why the modal is the right place
   for it and the inbox scan is not.
3. **Cold start is 7.4 s** on the first call after the server starts.
4. **`utils/__pycache__/laya_client.cpython-311.pyc`** is an orphan from a deleted
   `laya_client.py` created 2026-10-09 18:19. It is replaced by the new source file,
   or deleted if the new file is not written.

## Files

| File | Change |
|---|---|
| `utils/laya_client.py` | new — HTTP client |
| `app.py` | import + `POST /api/laya_text` after line 2003 |
| `templates/dashboard.html` | second button, branch in `handleTextAnalysis`, engine label |
| `.env.example` | five `LAYA_*` lines, documented as optional |