# megabrain — "user login password check"
repo `goldenrepo` · 1 core files (full code) · 3 related (mapped) · 0ms

## CORE

### 1. auth/login.py  `1.19`
linked: auth/session.py

**login_user, check_password** L1-13

## RELATED — best match + symbols per file · expand with `megabrain get <file> [--symbol NAME]` · code bodies: `--full`

### auth/session.py  `0.93` · matched: Session, open_session — A logged-in user session with an expiry.
- `class Session` L1-10
- `def __init__(self, user)` L4-6
- `def expire(self)` L8-10
- `def open_session(user)` L13-15

### billing/invoice.py  `0.75` · matched: create_invoice — Create a billing invoice for the given amount.
- `def create_invoice(amount)` L1-3

### util.py  `0.75` · matched: flatten — Flatten a nested list one level.
- `def flatten(xs)` L1-3
