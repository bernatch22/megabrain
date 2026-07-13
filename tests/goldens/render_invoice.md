# megabrain — "create billing invoice"
repo `goldenrepo` · 1 core files (full code) · 3 related (mapped) · 0ms

## CORE

### 1. billing/invoice.py  `1.17`

**create_invoice** L1-3
```python
def create_invoice(amount):
    """Create a billing invoice for the given amount."""
    return {"amount": amount, "status": "open"}
```

## RELATED — best match + symbols per file · expand with `megabrain get <file> [--symbol NAME]` · code bodies: `--full`

### auth/session.py  `0.79` · matched: Session, open_session — A logged-in user session with an expiry.
**Session, open_session** L1-15
- `class Session` L1-10
- `def __init__(self, user)` L4-6
- `def expire(self)` L8-10
- `def open_session(user)` L13-15

### auth/login.py  `0.75` · matched: login_user, check_password — Authenticate a user login with password check.
**login_user, check_password** L1-13
- `def login_user(name, password)` L4-8
- `def check_password(name, password)` L11-13

### util.py  `0.75` · matched: flatten — Flatten a nested list one level.
**flatten** L1-3
- `def flatten(xs)` L1-3
