# megabrain search — "user login password check"
repo `goldenrepo` · 4 signal chunks (0 pruned as noise) · 0ms

### 1. [1] auth/login.py L1-13 · login_user, check_password · `1.188`
```python
     1→from auth.session import open_session
     2→
     3→
     4→def login_user(name, password):
     5→    """Authenticate a user login with password check."""
     6→    if check_password(name, password):
     7→        return open_session(name)
     8→    return None
     9→
    10→
    11→def check_password(name, password):
    12→    """Verify the stored password hash for the user."""
    13→    return hash(password) % 7 == hash(name) % 7
```

### 2. [2] auth/session.py L1-15 · Session, open_session · `0.928`
```python
     1→class Session:
     2→    """A logged-in user session with an expiry."""
     3→
     4→    def __init__(self, user):
     5→        self.user = user
     6→        self.expired = False
     7→
     8→    def expire(self):
     9→        """Mark the session expired (logout)."""
    10→        self.expired = True
    11→
    12→
    13→def open_session(user):
    14→    """Create a fresh session for an authenticated user."""
    15→    return Session(user)
```

### 3. [3] billing/invoice.py L1-3 · create_invoice · `0.750`
```python
     1→def create_invoice(amount):
     2→    """Create a billing invoice for the given amount."""
     3→    return {"amount": amount, "status": "open"}
```

### 4. [4] util.py L1-3 · flatten · `0.750`
```python
     1→def flatten(xs):
     2→    """Flatten a nested list one level."""
     3→    return [y for x in xs for y in x]
```
