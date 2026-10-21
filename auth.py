import hashlib, hmac, re, secrets
from datetime import datetime, timedelta
from functools import wraps
import jwt
from flask import Blueprint, request, g
from werkzeug.security import generate_password_hash, check_password_hash
from audit import log
from config import cfg, cfg_int, utcnow, utcnow_precise
from db import q, one, ex, commit
from errors import ApiError
from notify import send_code

bp = Blueprint('auth', __name__)
def now(): return utcnow()
def secret():
    s = cfg('JWT_SECRET')
    if not s or len(s) < 16 or s.startswith('paste-'): raise RuntimeError('JWT_SECRET must be set to your own long random value (see backend/.env.example)')
    return s
def hcode(c): return hashlib.sha256((secret() + str(c)).encode()).hexdigest()
def digits(s): return re.sub(r'\D', '', str(s or ''))
def body(): return request.get_json(silent=True) or {}

def public(u):
    return {k: u[k] for k in ('id', 'name', 'email', 'phone', 'role', 'status', 'student_id')}

def current_user():
    h = request.headers.get('Authorization', '')
    try:
        p = jwt.decode(h[7:], secret(), algorithms=['HS256'])
        u = one("SELECT * FROM users WHERE id=%s AND status='active'", (int(p['sub']),))
    except Exception:
        u = None
    if not u: raise ApiError('Session expired. Please sign in again.', 401)
    return u

def login_required(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = current_user()
            if roles and u['role'] not in roles: raise ApiError('You do not have permission for this', 403)
            g.user = u
            return f(*a, **k)
        return w
    return deco

# ---- brute-force protection, stored in MySQL so it works across several server processes ----
def check_throttle(key, limit):
    r = one('SELECT n, reset_at FROM rate_limits WHERE k=%s', (key,))
    if r and r['reset_at'] > now() and r['n'] >= limit:
        raise ApiError('Too many attempts. Please try again in a few minutes.', 429)
def bump(key, window):
    r = one('SELECT n, reset_at FROM rate_limits WHERE k=%s FOR UPDATE', (key,))
    if r and r['reset_at'] > now(): ex('UPDATE rate_limits SET n=n+1 WHERE k=%s', (key,))
    else: ex('REPLACE INTO rate_limits (k, n, reset_at) VALUES (%s,1,%s)', (key, now() + timedelta(seconds=window)))
def clear(key): ex('DELETE FROM rate_limits WHERE k=%s', (key,))

def find_user(ident):
    ident = str(ident or '').strip(); d = digits(ident)
    return one('SELECT * FROM users WHERE (email IS NOT NULL AND LOWER(email)=%s) OR (phone IS NOT NULL AND %s<>\'\' AND phone=%s)', (ident.lower(), d, d))

def issue_code(uid, kind, ttl):
    code = str(secrets.randbelow(900000) + 100000)
    ex('REPLACE INTO auth_codes (user_id, kind, code_hash, expires_at, attempts) VALUES (%s,%s,%s,%s,0)', (uid, kind, hcode(code), utcnow_precise() + timedelta(seconds=ttl)))
    return code
def consume_code(uid, kind, code):
    r = one('SELECT * FROM auth_codes WHERE user_id=%s AND kind=%s FOR UPDATE', (uid, kind))
    bad = ApiError('Invalid or expired code', 400, keep=True)
    if not r or r['expires_at'] < utcnow_precise() or r['attempts'] >= 5: raise bad
    if not hmac.compare_digest(r['code_hash'], hcode(code)):
        ex('UPDATE auth_codes SET attempts=attempts+1 WHERE user_id=%s AND kind=%s', (uid, kind)); raise bad
    ex('DELETE FROM auth_codes WHERE user_id=%s AND kind=%s', (uid, kind))

def start_session(u):
    last = {'at': u['last_login_at'], 'ip': u['last_login_ip']} if u['last_login_at'] else None
    ex('UPDATE users SET last_login_at=%s, last_login_ip=%s WHERE id=%s', (now(), request.remote_addr, u['id']))
    log('Login', user=u)
    tok = jwt.encode({'sub': str(u['id']), 'exp': now() + timedelta(hours=12)}, secret(), algorithm='HS256')
    return {'token': tok, 'user': public(u), 'last': last}

@bp.post('/auth/login')
def login():
    b = body(); ident = str(b.get('id', '')).strip(); key = f'login:{request.remote_addr}:{ident.lower()}'
    check_throttle(key, 5)
    u = find_user(ident)
    if not u or not check_password_hash(u['pw_hash'], str(b.get('pw', ''))):
        bump(key, 900); raise ApiError('Invalid email/phone or password', 401, keep=True)
    if u['status'] != 'active': raise ApiError('Your account is awaiting admin approval', 403)
    clear(key)
    if u['role'] == 'Admin':
        code = issue_code(u['id'], '2fa', 600); send_code(u, code, '2fa'); log('2FA code issued', user=u)
        return {'need2fa': True, 'uid': u['id'], 'demoCode': code if cfg('SHOW_CODES') == '1' else None}
    return start_session(u)

@bp.post('/auth/verify')
def verify():
    b = body(); u = one("SELECT * FROM users WHERE id=%s AND role='Admin' AND status='active'", (b.get('uid'),))
    if not u: raise ApiError('Invalid or expired code', 400)
    consume_code(u['id'], '2fa', b.get('code', '')); return start_session(u)

@bp.post('/auth/register')
def register():
    b = body(); name = re.sub(r'\s+', ' ', str(b.get('name', ''))).strip(); email = str(b.get('email', '')).strip().lower() or None
    phone = digits(b.get('phone')) or None; role = b.get('role'); pw = str(b.get('pw', ''))
    if len(name) < 2 or len(name) > 100: raise ApiError('Enter your full name')
    if not (email or phone): raise ApiError('Enter an email or a phone number')
    if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email): raise ApiError('Enter a valid email address')
    if phone and not 9 <= len(phone) <= 15: raise ApiError('Phone must have 9 to 15 digits')
    if len(pw) < 8: raise ApiError('Password must be at least 8 characters')
    if role not in ('Parent', 'Teacher', 'Staff'): raise ApiError('Choose Parent, Teacher or Staff')
    if one('SELECT id FROM users WHERE (email IS NOT NULL AND email=%s) OR (phone IS NOT NULL AND phone=%s)', (email, phone)):
        raise ApiError('That email or phone is already registered', 409)
    sid, status = None, 'pending'
    if role == 'Parent':
        s = one('SELECT id FROM students WHERE LOWER(adm)=%s AND phone=%s', (str(b.get('adm', '')).strip().lower(), phone or ''))
        if not s: raise ApiError('Admission number and guardian phone do not match our records')
        sid, status = s['id'], 'active'
    uid = ex('INSERT INTO users (name,email,phone,pw_hash,role,status,student_id,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
             (name, email, phone, generate_password_hash(pw), role, status, sid, now()))
    log('Self registration', f'role={role.lower()}', user={'name': name, 'role': role})
    return {'msg': 'Account created. You can sign in now.' if status == 'active' else 'Registration received. An administrator must approve it first.'}, 201

@bp.post('/auth/forgot')
def forgot():
    ident = str(body().get('id', '')).strip(); key = f'forgot:{request.remote_addr}:{ident.lower()}'
    check_throttle(key, 5); bump(key, 900)
    u = find_user(ident); code = None
    if u and u['status'] == 'active':
        code = issue_code(u['id'], 'reset', cfg_int('RESET_TTL_SECONDS', 60)); send_code(u, code, 'reset'); log('Password reset requested', user=u)
    commit()
    return {'ok': True, 'demoCode': code if cfg('SHOW_CODES') == '1' else None, 'expires_in': cfg_int('RESET_TTL_SECONDS', 60)}

@bp.post('/auth/reset')
def reset():
    b = body(); u = find_user(b.get('id')); pw = str(b.get('pw', ''))
    if not u: raise ApiError('Invalid or expired code', 400)
    consume_code(u['id'], 'reset', str(b.get('code', '')))
    if len(pw) < 8: raise ApiError('Password must be at least 8 characters')
    ex('UPDATE users SET pw_hash=%s WHERE id=%s', (generate_password_hash(pw), u['id'])); log('Password reset', user=u)
    return {'ok': True}

@bp.post('/auth/password')
@login_required()
def change_password():
    b = body(); u = g.user
    if not check_password_hash(u['pw_hash'], str(b.get('old', ''))): raise ApiError('Current password is wrong')
    if len(str(b.get('new', ''))) < 8: raise ApiError('New password must be at least 8 characters')
    ex('UPDATE users SET pw_hash=%s WHERE id=%s', (generate_password_hash(b['new']), u['id'])); log('Password changed'); return {'ok': True}

@bp.get('/auth/me')
@login_required()
def me():
    return {'user': public(g.user)}
