# megabrain — "user login password check"
repo `goldenrepo` · 1 core files (full code) · 3 related (mapped) · 0ms

## CORE

### 1. auth/login.py  `1.19`
linked: auth/session.py

**login_user, check_password** L1-13
```python
from auth.session import open_session


def login_user(name, password):
    """Authenticate a user login with password check."""
    if check_password(name, password):
        return open_session(name)
    return None


def check_password(name, password):
    """Verify the stored password hash for the user."""
    return hash(password) % 7 == hash(name) % 7
```

## RELATED — best match + symbols per file · expand with `megabrain get <file> [--symbol NAME]`

### auth/session.py  `0.93` · matched: Session, open_session — A logged-in user session with an expiry.
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
- `class Session` L1-10
- `def __init__(self, user)` L4-6
- `def expire(self)` L8-10
- `def open_session(user)` L13-15

### billing/invoice.py  `0.75` · matched: create_invoice — Create a billing invoice for the given amount.
**create_invoice** L1-3
```python
def create_invoice(amount):
    """Create a billing invoice for the given amount."""
    return {"amount": amount, "status": "open"}
```
- `def create_invoice(amount)` L1-3

### util.py  `0.75` · matched: flatten — Flatten a nested list one level.
**flatten** L1-3
```python
def flatten(xs):
    """Flatten a nested list one level."""
    return [y for x in xs for y in x]
```
- `def flatten(xs)` L1-3
