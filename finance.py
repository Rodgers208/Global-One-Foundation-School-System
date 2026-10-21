import base64, random, requests
from datetime import datetime, date
from flask import Blueprint, request, g
from audit import log
from auth import login_required, body
from config import cfg, utcnow
from db import q, one, ex
from errors import ApiError
from settings import fee_year, get_settings

bp = Blueprint('finance', __name__)
METHODS = ('M-Pesa', 'Cash', 'Bank', 'Card')

def balance_of(sid):
    r = one('''SELECT (SELECT COALESCE(SUM(f.amount),0) FROM fee_structures f WHERE f.class_id=s.class_id AND f.year=%s) AS expected,
                      (SELECT COALESCE(SUM(p.amount),0) FROM payments p WHERE p.student_id=s.id) AS paid FROM students s WHERE s.id=%s''', (fee_year(), sid))
    return {'expected': r['expected'], 'paid': r['paid'], 'balance': r['expected'] - r['paid']}

# ---- fee structures ----
@bp.get('/fee-structures')
@login_required('Admin')
def fees():
    return {'rows': q('SELECT f.id, f.class_id, c.name AS class_name, f.term, f.year, f.amount FROM fee_structures f JOIN classes c ON c.id=f.class_id ORDER BY c.sort_order, f.year, f.term')}
@bp.post('/fee-structures')
@login_required('Admin')
def save_fee():
    b = body(); amt = b.get('amount'); term = b.get('term')
    try: amt = float(amt); year = int(b.get('year')); cid = int(b.get('class_id'))
    except (TypeError, ValueError): raise ApiError('Choose a class and enter the year and amount')
    if not (0 < amt < 10_000_000) or term not in ('Term 1', 'Term 2', 'Term 3') or not 2000 <= year <= 2100 or not one('SELECT 1 FROM classes WHERE id=%s', (cid,)): raise ApiError('Invalid fee details')
    ex('INSERT INTO fee_structures (class_id, term, year, amount) VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE amount=VALUES(amount)', (cid, term, year, amt)); log('Fee structure saved', f'class {cid} {term} {year}')
    return {'ok': True}
@bp.delete('/fee-structures/<int:fid>')
@login_required('Admin')
def del_fee(fid): ex('DELETE FROM fee_structures WHERE id=%s', (fid,)); log('Fee structure deleted', str(fid)); return {'ok': True}

# ---- payments ----
def pay_row(pid):
    return one('''SELECT p.id, p.student_id, s.adm, s.name AS student_name, c.name AS class_name, p.amount, p.paid_on, p.method, p.ref
                  FROM payments p JOIN students s ON s.id=p.student_id JOIN classes c ON c.id=s.class_id WHERE p.id=%s''', (pid,))
@bp.get('/payments')
@login_required()
def payments():
    u = g.user; sid = request.args.get('student_id', type=int); size = 100000 if request.args.get('all') else 25
    if u['role'] == 'Parent': sid = u['student_id']
    elif u['role'] != 'Admin': raise ApiError('You do not have permission for this', 403)
    w, a = ('WHERE p.student_id=%s', [sid]) if sid else ('', [])
    rows = q(f'''SELECT p.id, p.student_id, s.adm, s.name AS student_name, c.name AS class_name, p.amount, p.paid_on, p.method, p.ref
                 FROM payments p JOIN students s ON s.id=p.student_id JOIN classes c ON c.id=s.class_id {w} ORDER BY p.id DESC LIMIT %s''', a + [size])
    return {'rows': rows}
@bp.post('/payments')
@login_required('Admin')
def record_payment():
    b = body(); adm = str(b.get('adm', '')).strip()
    s = one('SELECT id FROM students WHERE adm=%s', (adm,)) if adm else one('SELECT id FROM students WHERE id=%s', (b.get('student_id'),))
    if not s: raise ApiError('Unknown admission number')
    try: amt = round(float(b.get('amount')), 2)
    except (TypeError, ValueError): raise ApiError('Enter an amount')
    if not 0 < amt < 10_000_000 or b.get('method') not in METHODS: raise ApiError('Enter a valid amount and payment method')
    ref = 'RCP' + str(random.randint(10**6, 10**7 - 1))
    pid = ex('INSERT INTO payments (student_id, amount, paid_on, method, ref, created_by) VALUES (%s,%s,%s,%s,%s,%s)', (s['id'], amt, date.today(), b['method'], ref, g.user['id']))
    log('Payment recorded', f'{adm or s["id"]} KES {amt}'); return {'payment': pay_row(pid)}, 201
@bp.get('/payments/<int:pid>/receipt')
@login_required()
def receipt(pid):
    p = pay_row(pid)
    if not p: raise ApiError('Payment not found', 404)
    if g.user['role'] == 'Parent' and g.user['student_id'] != p['student_id']: raise ApiError('Not allowed', 403)
    if g.user['role'] not in ('Admin', 'Parent'): raise ApiError('Not allowed', 403)
    return {'payment': p, 'balance': balance_of(p['student_id'])['balance'], 'school': get_settings()}

# ---- reports ----
@bp.get('/reports/defaulters')
@login_required('Admin')
def defaulters():
    cid = request.args.get('class_id', type=int); lim = 100000 if request.args.get('all') else 200
    rows = q('''SELECT s.id, s.adm, s.name, c.name AS class_name, s.guardian, s.phone, COALESCE(f.total,0)-COALESCE(p.paid,0) AS balance
                FROM students s JOIN classes c ON c.id=s.class_id
                LEFT JOIN (SELECT class_id, SUM(amount) total FROM fee_structures WHERE year=%s GROUP BY class_id) f ON f.class_id=s.class_id
                LEFT JOIN (SELECT student_id, SUM(amount) paid FROM payments GROUP BY student_id) p ON p.student_id=s.id
                WHERE s.status='Active' AND COALESCE(f.total,0)-COALESCE(p.paid,0)>0 AND (%s IS NULL OR s.class_id=%s) ORDER BY balance DESC LIMIT %s''', (fee_year(), cid, cid, lim))
    return {'rows': rows, 'total_balance': sum(r['balance'] for r in rows)}
@bp.get('/reports/dashboard')
@login_required('Admin', 'Teacher', 'Staff')
def dashboard():
    y = fee_year(); u = g.user
    out = {'students': one('SELECT COUNT(*) n FROM students')['n'], 'inventory': one('SELECT COUNT(*) n FROM inventory_items')['n']}
    if u['role'] != 'Admin': return out
    out['collected'] = one('SELECT COALESCE(SUM(amount),0) t FROM payments')['t']
    fee = {r['class_id']: r['t'] for r in q('SELECT class_id, SUM(amount) t FROM fee_structures WHERE year=%s GROUP BY class_id', (y,))}
    col = {r['class_id']: r['t'] for r in q('SELECT s.class_id, SUM(p.amount) t FROM payments p JOIN students s ON s.id=p.student_id GROUP BY s.class_id')}
    cnt = {r['class_id']: r['n'] for r in q('SELECT class_id, COUNT(*) n FROM students WHERE status=%s GROUP BY class_id', ('Active',))}
    out['by_class'] = [{'name': c['name'], 'expected': (fee.get(c['id'], 0) or 0) * cnt.get(c['id'], 0), 'collected': col.get(c['id'], 0) or 0} for c in q('SELECT id, name FROM classes ORDER BY sort_order, id')]
    total_exp = sum(r['expected'] for r in out['by_class'])
    paid = {r['m']: r['t'] for r in q('SELECT MONTH(paid_on) m, SUM(amount) t FROM payments WHERE YEAR(paid_on)=%s GROUP BY MONTH(paid_on)', (y,))}
    out['by_month'] = [{'name': n, 'expected': total_exp / 12, 'collected': paid.get(i + 1, 0)} for i, n in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())]
    out['year'] = y; return out

# ---- M-Pesa STK push (Safaricom Daraja) ----
def base(): return 'https://api.safaricom.co.ke' if cfg('DARAJA_ENV') == 'production' else 'https://sandbox.safaricom.co.ke'
@bp.post('/mpesa/stk')
@login_required('Parent')
def stk():
    b = body(); sid = g.user['student_id']
    try: amt = int(round(float(b.get('amount'))))
    except (TypeError, ValueError): raise ApiError('Enter an amount')
    ph = '254' + ''.join(ch for ch in str(b.get('phone', '')) if ch.isdigit())[-9:]
    if amt < 1 or not sid or len(ph) != 12: raise ApiError('Enter a valid amount and M-Pesa number')
    try:
        t = requests.get(base() + '/oauth/v1/generate?grant_type=client_credentials', auth=(cfg('DARAJA_KEY', ''), cfg('DARAJA_SECRET', '')), timeout=15).json()['access_token']
        ts = utcnow().strftime('%Y%m%d%H%M%S'); sc = cfg('DARAJA_SHORTCODE', '')
        r = requests.post(base() + '/mpesa/stkpush/v1/processrequest', headers={'Authorization': 'Bearer ' + t}, timeout=20, json={
            'BusinessShortCode': sc, 'Password': base64.b64encode((sc + cfg('DARAJA_PASSKEY', '') + ts).encode()).decode(), 'Timestamp': ts,
            'TransactionType': 'CustomerPayBillOnline', 'Amount': amt, 'PartyA': ph, 'PartyB': sc, 'PhoneNumber': ph,
            'CallBackURL': cfg('PUBLIC_URL', '') + '/api/mpesa/callback', 'AccountReference': str(sid), 'TransactionDesc': 'School fees'}).json()
    except Exception: raise ApiError('M-Pesa is unavailable right now. Please try again.', 502)
    if r.get('ResponseCode') != '0': raise ApiError(r.get('errorMessage') or r.get('ResponseDescription') or 'M-Pesa request failed', 502)
    ex('INSERT INTO mpesa_pending (checkout_id, student_id, amount, created_at) VALUES (%s,%s,%s,%s)', (r['CheckoutRequestID'], sid, amt, utcnow()))
    return {'msg': 'Check your phone and enter your M-Pesa PIN. Your balance updates once the payment is confirmed.'}
@bp.post('/mpesa/callback')
def callback():
    c = ((request.get_json(silent=True) or {}).get('Body') or {}).get('stkCallback') or {}
    p = one('SELECT * FROM mpesa_pending WHERE checkout_id=%s FOR UPDATE', (c.get('CheckoutRequestID'),))
    if p:
        if c.get('ResultCode') == 0:
            m = {i.get('Name'): i.get('Value') for i in (c.get('CallbackMetadata') or {}).get('Item', [])}
            if m.get('MpesaReceiptNumber') and not one('SELECT 1 FROM payments WHERE ref=%s', (m['MpesaReceiptNumber'],)):
                ex('INSERT INTO payments (student_id, amount, paid_on, method, ref) VALUES (%s,%s,%s,%s,%s)', (p['student_id'], m.get('Amount', p['amount']), date.today(), 'M-Pesa', m['MpesaReceiptNumber']))
        ex('DELETE FROM mpesa_pending WHERE checkout_id=%s', (p['checkout_id'],))
    return {'ResultCode': 0, 'ResultDesc': 'OK'}
