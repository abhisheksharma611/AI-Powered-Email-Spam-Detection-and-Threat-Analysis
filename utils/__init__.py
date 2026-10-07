"""Shared helpers: Gmail client, auth, AI explanation, keyword learning.

This __init__.py is load-bearing, not decoration.

Without it `utils` is a PEP 420 namespace package, and namespace packages lose
to regular packages during the path scan regardless of sys.path order. The repo
also contains `models/utils/`, which has an __init__.py and is therefore regular.
A script run as `python models/roberta_train.py` puts `models/` first on
sys.path, so `from utils.gmail_client import ...` resolved to
`models/utils/` and raised ModuleNotFoundError -- while the same import worked
from app.py, which runs from the repo root and never sees models/ on the path.

Making this a regular package fixes it: with both regular, the first sys.path
entry wins, and every script inserts the repo root at position 0.
"""