from datetime import date
from db import q, ex
DEFAULTS = {'name': 'Global One Foundation School', 'motto': '', 'mission': '', 'term': 'Term 3', 'logo': ''}
def get_settings():
    s = dict(DEFAULTS); s.update({r['k']: r['v'] for r in q('SELECT k, v FROM settings')}); return s
def fee_year(): return date.today().year
