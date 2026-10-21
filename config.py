import os
def cfg(k, d=None): return os.environ.get(k, d)
def cfg_int(k, d): return int(os.environ.get(k, d))

from datetime import datetime, timezone
def utcnow(): return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
def utcnow_precise(): return datetime.now(timezone.utc).replace(tzinfo=None)
