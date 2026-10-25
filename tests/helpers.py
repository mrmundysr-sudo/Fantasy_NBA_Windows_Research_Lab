import os, tempfile, threading
from nbalab.lab import Lab

_lock = threading.Lock()
_lab = None

def shared_lab():
    """One SYNTHETIC lab per test process (building it takes a few seconds). Tests that mutate use fresh_lab()."""
    global _lab
    with _lock:
        if _lab is None:
            _lab = Lab.open(os.path.join(tempfile.mkdtemp(), "shared.sqlite"))
            _lab.train()
        return _lab

def fresh_lab():
    lab = Lab.open(os.path.join(tempfile.mkdtemp(), "fresh.sqlite"))
    lab.train()
    return lab
