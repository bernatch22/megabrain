# megabrain — "how does a session expire on logout"
repo `goldenrepo` · 1 core files (full code) · 3 related (mapped) · 0ms

## CORE

### 1. auth/session.py  `1.18`
linked: auth/login.py

**Session, open_session** L1-15
```python
class Session:
    """A logged-in user session with an expiry."""

    def __init__(self, user):
        self.user = user
        self.expired = False

    def expire(self):
        """Mark the session expired (logout)."""
        self.expired = True


def open_session(user):
    """Create a fresh session for an authenticated user."""
    return Session(user)
```

## RELATED — best match + symbols per file · expand with `megabrain get <file> [--symbol NAME]` · code bodies: `--full`

### auth/login.py  `0.83` · matched: login_user, check_password — Authenticate a user login with password check.
**login_user, check_password** L1-13
- `def login_user(name, password)` L4-8
- `def check_password(name, password)` L11-13

### util.py  `0.80` · matched: flatten — Flatten a nested list one level.
**flatten** L1-3
- `def flatten(xs)` L1-3

### billing/invoice.py  `0.79` · matched: create_invoice — Create a billing invoice for the given amount.
**create_invoice** L1-3
- `def create_invoice(amount)` L1-3
