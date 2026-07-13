# megabrain prune — "user login password check"
repo `goldenrepo` · 4 signal chunks (0 pruned as noise) · 0ms

### 1. [1] auth/login.py L1-13 · login_user, check_password · `1.188`
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

### 2. [2] auth/session.py L1-15 · Session, open_session · `0.928`
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

### 3. [3] billing/invoice.py L1-3 · create_invoice · `0.750`
```python
def create_invoice(amount):
    """Create a billing invoice for the given amount."""
    return {"amount": amount, "status": "open"}
```

### 4. [4] util.py L1-3 · flatten · `0.750`
```python
def flatten(xs):
    """Flatten a nested list one level."""
    return [y for x in xs for y in x]
```
