from config import utcnow
from flask import request, g
from db import ex
def log(action, details='', user=None):
    u = user or getattr(g, 'user', None)
    ex('INSERT INTO audit_log (at, actor_name, actor_role, action, details, ip) VALUES (%s,%s,%s,%s,%s,%s)',
       (utcnow(), u['name'] if u else '', u['role'] if u else '', action[:60], (details or '-')[:255], (request.remote_addr or '')[:45]))
