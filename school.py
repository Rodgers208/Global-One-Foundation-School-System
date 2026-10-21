import secrets
from datetime import datetime
from flask import Blueprint, request, g
from werkzeug.security import generate_password_hash
from audit import log
from auth import login_required, body, now, public
from db import q, one, ex
from errors import ApiError
import grading
from settings import get_settings, fee_year, DEFAULTS

bp = Blueprint('school', __name__)
TERMS = ('Term 1', 'Term 2', 'Term 3')
def bands_json():
    B = grading.BANDS
    return [{'min': b[0], 'level': b[1], 'points': b[2], 'name': grading.LEVEL_NAMES[b[1]],
             'range': f'{b[0]} to under {B[i + 1][0]}' if i + 1 < len(B) else f'{b[0]} to 100'} for i, b in enumerate(B)][::-1]

@bp.get('/meta')
@login_required()
def meta():
    s = get_settings(); s.pop('logo', None) if request.args.get('nologo') else None
    return {'classes': q('SELECT id, name FROM classes ORDER BY sort_order, id'), 'subjects': q('SELECT id, name FROM subjects ORDER BY id'),
            'categories': q('SELECT id, name FROM inventory_categories ORDER BY name'), 'settings': s, 'year': fee_year(),
            'bands': bands_json()}
@bp.get('/public')
def public_info():
    s = get_settings(); return {'name': s['name']}
@bp.put('/settings')
@login_required('Admin')
def save_settings():
    b = body()
    for k in ('name', 'motto', 'mission', 'term', 'logo'):
        if k not in b: continue
        v = str(b[k]).strip()
        if k == 'term' and v not in TERMS: raise ApiError('Invalid term')
        if k == 'logo' and (len(v) > 1_500_000 or (v and not v.startswith('data:image/'))): raise ApiError('Logo must be an image under 1 MB')
        if k in ('name', 'motto') and len(v) > 150 or k == 'mission' and len(v) > 500: raise ApiError(f'{k} is too long')
        ex('INSERT INTO settings (k, v) VALUES (%s,%s) ON DUPLICATE KEY UPDATE v=VALUES(v)', (k, v))
    log('School profile updated'); return {'settings': get_settings()}

# ---- classes / subjects ----
@bp.post('/classes')
@login_required('Admin')
def add_class():
    n = ' '.join(str(body().get('name', '')).split())
    if not 2 <= len(n) <= 40: raise ApiError('Enter a class name')
    if one('SELECT 1 FROM classes WHERE LOWER(name)=%s', (n.lower(),)): raise ApiError('That class already exists', 409)
    ex('INSERT INTO classes (name, sort_order) VALUES (%s, (SELECT COALESCE(MAX(sort_order),0)+1 FROM (SELECT sort_order FROM classes) t))', (n,)); return {'ok': True}, 201
@bp.get('/classes')
@login_required('Admin', 'Teacher', 'Staff')
def classes():
    return {'rows': q('''SELECT c.id, c.name, COUNT(s.id) AS students,
        (SELECT COALESCE(SUM(f.amount),0) FROM fee_structures f WHERE f.class_id=c.id AND f.year=%s) AS fee FROM classes c LEFT JOIN students s ON s.class_id=c.id GROUP BY c.id ORDER BY c.sort_order, c.id''', (fee_year(),))}
@bp.delete('/classes/<int:cid>')
@login_required('Admin')
def del_class(cid):
    if one('SELECT 1 FROM students WHERE class_id=%s LIMIT 1', (cid,)): raise ApiError('Move the students out of this class first', 409)
    ex('DELETE FROM classes WHERE id=%s', (cid,)); return {'ok': True}
@bp.post('/subjects')
@login_required('Admin')
def add_subject():
    n = ' '.join(str(body().get('name', '')).split())
    if not 2 <= len(n) <= 60: raise ApiError('Enter a subject name')
    if one('SELECT 1 FROM subjects WHERE LOWER(name)=%s', (n.lower(),)): raise ApiError('That subject already exists', 409)
    ex('INSERT INTO subjects (name) VALUES (%s)', (n,)); return {'ok': True}, 201
@bp.delete('/subjects/<int:sid>')
@login_required('Admin')
def del_subject(sid): ex('DELETE FROM subjects WHERE id=%s', (sid,)); return {'ok': True}

# ---- inventory ----
@bp.get('/inventory')
@login_required('Admin', 'Staff')
def inventory():
    t = request.args.get('q', '').strip(); like = f'%{t}%'
    return {'rows': q('''SELECT i.id, i.name, i.category_id, c.name AS category, i.location, i.qty, i.damaged FROM inventory_items i
                         LEFT JOIN inventory_categories c ON c.id=i.category_id WHERE (%s='' OR i.name LIKE %s OR c.name LIKE %s OR i.location LIKE %s) ORDER BY i.id''', (t, like, like, like))}
@bp.post('/inventory')
@login_required('Admin', 'Staff')
def add_item():
    b = body(); n = str(b.get('name', '')).strip()
    try: qty = int(b.get('qty') or 0)
    except ValueError: raise ApiError('Quantity must be a number')
    if not 1 <= len(n) <= 120 or not 0 <= qty <= 10**6: raise ApiError('Enter the item name and a valid quantity')
    ex('INSERT INTO inventory_items (name, category_id, location, qty) VALUES (%s,%s,%s,%s)', (n, b.get('category_id') or None, str(b.get('location', ''))[:80], qty)); log('Inventory item added', n); return {'ok': True}, 201
@bp.post('/inventory/<int:iid>/adjust')
@login_required('Admin', 'Staff')
def adjust(iid):
    d = body().get('delta')
    if d not in (1, -1): raise ApiError('Invalid change')
    ex('UPDATE inventory_items SET qty=GREATEST(0, qty+%s) WHERE id=%s', (d, iid)); return {'ok': True}
@bp.post('/inventory/<int:iid>/damage')
@login_required('Admin', 'Staff')
def damage(iid):
    ex('UPDATE inventory_items SET qty=qty-1, damaged=damaged+1 WHERE id=%s AND qty>0', (iid,)); return {'ok': True}
@bp.delete('/inventory/<int:iid>')
@login_required('Admin', 'Staff')
def del_item(iid): ex('DELETE FROM inventory_items WHERE id=%s', (iid,)); log('Inventory item deleted', str(iid)); return {'ok': True}
@bp.post('/inventory/categories')
@login_required('Admin', 'Staff')
def add_cat():
    n = ' '.join(str(body().get('name', '')).split())
    if not 2 <= len(n) <= 60: raise ApiError('Enter a category name')
    ex('INSERT IGNORE INTO inventory_categories (name) VALUES (%s)', (n,)); return {'ok': True}, 201

# ---- announcements ----
AUD = {'Admin': ('All', 'Teachers', 'Parents'), 'Teacher': ('All', 'Teachers'), 'Staff': ('All',), 'Parent': ('All', 'Parents')}
@bp.get('/announcements')
@login_required()
def announcements():
    u = g.user; aud = AUD[u['role']]
    if u['role'] == 'Admin':
        rows = q('SELECT a.*, (SELECT COUNT(*) FROM announcement_reads r WHERE r.announcement_id=a.id) AS read_count FROM announcements a ORDER BY a.id DESC LIMIT 200')
    else:
        rows = q(f'SELECT a.*, (r.user_id IS NOT NULL) AS is_read FROM announcements a LEFT JOIN announcement_reads r ON r.announcement_id=a.id AND r.user_id=%s WHERE a.audience IN ({",".join(["%s"]*len(aud))}) ORDER BY a.id DESC LIMIT 200', (u['id'], *aud))
        for r in rows: r['unread'] = not r.pop('is_read'); r.pop('total'); r.pop('delivered')
    return {'rows': rows}
@bp.post('/announcements')
@login_required('Admin')
def add_announcement():
    b = body(); t = str(b.get('title', '')).strip(); m = str(b.get('body', '')).strip(); a = b.get('audience')
    if not 1 <= len(t) <= 150 or not 1 <= len(m) <= 5000 or a not in ('All', 'Teachers', 'Parents'): raise ApiError('Enter a title, a message and an audience')
    kids = one('SELECT COUNT(*) n, SUM(CHAR_LENGTH(phone)>=9) ok FROM students')
    teachers = one("SELECT COUNT(*) n FROM users WHERE role='Teacher' AND status='active'")['n']; ok = int(kids['ok'] or 0)
    total, delivered = {'Teachers': (teachers, teachers), 'Parents': (kids['n'], ok), 'All': (kids['n'] + teachers, ok + teachers)}[a]
    ex('INSERT INTO announcements (title, body, audience, by_name, created_at, total, delivered) VALUES (%s,%s,%s,%s,%s,%s,%s)', (t, m, a, g.user['name'], now(), total, delivered))
    log('Announcement posted', t[:80]); return {'ok': True}, 201
@bp.put('/announcements/<int:aid>')
@login_required('Admin')
def edit_announcement(aid):
    b = body(); t = str(b.get('title', '')).strip(); m = str(b.get('body', '')).strip()
    if not t or not m or b.get('audience') not in ('All', 'Teachers', 'Parents'): raise ApiError('Enter a title, a message and an audience')
    ex('UPDATE announcements SET title=%s, body=%s, audience=%s WHERE id=%s', (t, m, b['audience'], aid)); return {'ok': True}
@bp.delete('/announcements/<int:aid>')
@login_required('Admin')
def del_announcement(aid): ex('DELETE FROM announcements WHERE id=%s', (aid,)); log('Announcement deleted', str(aid)); return {'ok': True}
@bp.post('/announcements/<int:aid>/read')
@login_required('Teacher', 'Staff', 'Parent')
def mark_read(aid):
    a = one('SELECT audience FROM announcements WHERE id=%s', (aid,))
    if not a or a['audience'] not in AUD[g.user['role']]: raise ApiError('Announcement not found', 404)
    ex('INSERT IGNORE INTO announcement_reads (announcement_id, user_id, read_at) VALUES (%s,%s,%s)', (aid, g.user['id'], now())); return {'ok': True}

# ---- results: CBC junior-school grading (3 exams per subject per term). All calculations live in grading.py ----
def args_term():
    term = request.args.get('term') or body().get('term')
    try: year = int(request.args.get('year') or body().get('year') or fee_year())
    except ValueError: raise ApiError('Invalid year')
    if term not in TERMS: raise ApiError('Choose a term')
    return term, year
def class_sheet(cid, term, year):
    """Every student in the class with per-subject mean/level/points, total, total points and class position."""
    studs = q('SELECT id, adm, name FROM students WHERE class_id=%s ORDER BY id', (cid,)); subs = q('SELECT id, name FROM subjects ORDER BY id'); marks = {}
    for r in q('SELECT r.student_id, r.subject_id, r.exam, r.mark FROM results r JOIN students s ON s.id=r.student_id WHERE s.class_id=%s AND r.year=%s AND r.term=%s', (cid, year, term)):
        marks.setdefault(r['student_id'], {}).setdefault(r['subject_id'], [None, None, None])[r['exam'] - 1] = float(r['mark'])
    out = []
    for s in studs:
        per = {sub['id']: grading.subject_summary(marks.get(s['id'], {}).get(sub['id'], [None] * 3)) for sub in subs}
        out.append({**s, 'subjects': per, **grading.student_total(list(per.values()))})
    pos = grading.rank({s['id']: s['total'] for s in out})
    for s in out: s['position'] = pos.get(s['id'])
    return out, subs, len(pos)
@bp.get('/results')
@login_required('Admin', 'Teacher')
def results():
    cid = request.args.get('class_id', type=int); term, year = args_term(); page = max(request.args.get('page', 1, type=int), 1); size = 50
    if not one('SELECT 1 FROM classes WHERE id=%s', (cid,)): raise ApiError('Choose a class')
    sheet, _, ranked = class_sheet(cid, term, year)
    pub = bool(one('SELECT 1 FROM result_publications WHERE class_id=%s AND year=%s AND term=%s', (cid, year, term)))
    return {'rows': sheet[(page - 1) * size: page * size], 'total': len(sheet), 'published': pub, 'year': year, 'ranked': ranked}
@bp.put('/results')
@login_required('Admin', 'Teacher')
def set_mark():
    b = body(); term, year = args_term(); m = b.get('mark')
    if b.get('exam') not in (1, 2, 3): raise ApiError('Exam must be 1, 2 or 3')
    if not one('SELECT 1 FROM students WHERE id=%s', (b.get('student_id'),)) or not one('SELECT 1 FROM subjects WHERE id=%s', (b.get('subject_id'),)): raise ApiError('Unknown student or subject')
    if m in (None, ''):
        ex('DELETE FROM results WHERE student_id=%s AND subject_id=%s AND year=%s AND term=%s AND exam=%s', (b['student_id'], b['subject_id'], year, term, b['exam'])); return {'ok': True}
    try: m = round(float(m), 2)
    except (TypeError, ValueError): raise ApiError('Marks must be a number from 0 to 100')
    if not 0 <= m <= 100: raise ApiError('Marks must be from 0 to 100')
    ex('INSERT INTO results (student_id, subject_id, year, term, exam, mark) VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE mark=VALUES(mark)', (b['student_id'], b['subject_id'], year, term, b['exam'], m)); return {'ok': True}
@bp.post('/results/publish')
@login_required('Admin')
def publish():
    b = body(); term, year = args_term(); cid = b.get('class_id')
    if not one('SELECT 1 FROM classes WHERE id=%s', (cid,)): raise ApiError('Unknown class')
    if b.get('published'): ex('INSERT IGNORE INTO result_publications (class_id, year, term) VALUES (%s,%s,%s)', (cid, year, term))
    else: ex('DELETE FROM result_publications WHERE class_id=%s AND year=%s AND term=%s', (cid, year, term))
    log('Results ' + ('published' if b.get('published') else 'unpublished'), f'class {cid} {term} {year}'); return {'ok': True}
@bp.get('/results/published/<int:sid>')
@login_required()
def published_terms(sid):
    if g.user['role'] == 'Parent' and g.user['student_id'] != sid: raise ApiError('Not allowed', 403)
    return {'rows': q('SELECT DISTINCT p.year, p.term FROM result_publications p JOIN students s ON s.class_id=p.class_id AND s.id=%s JOIN results r ON r.student_id=s.id AND r.year=p.year AND r.term=p.term ORDER BY p.year DESC, p.term', (sid,))}
@bp.get('/results/report/<int:sid>')
@login_required()
def report_card(sid):
    term, year = args_term(); u = g.user
    s = one('SELECT s.id, s.adm, s.name, s.class_id, c.name AS class_name FROM students s JOIN classes c ON c.id=s.class_id WHERE s.id=%s', (sid,))
    if not s: raise ApiError('Student not found', 404)
    if u['role'] == 'Parent':
        if u['student_id'] != sid: raise ApiError('Not allowed', 403)
        if not one('SELECT 1 FROM result_publications WHERE class_id=%s AND year=%s AND term=%s', (s['class_id'], year, term)): raise ApiError('Results have not been published yet', 403)
    elif u['role'] not in ('Admin', 'Teacher'): raise ApiError('Not allowed', 403)
    sheet, subs, ranked = class_sheet(s['class_id'], term, year); me = next(x for x in sheet if x['id'] == sid)
    rows = [{'subject': sub['name'], **me['subjects'][sub['id']]} for sub in subs if any(x is not None for x in me['subjects'][sub['id']]['exams'])]
    return {'student': s, 'subjects': rows, 'total': me['total'], 'total_points': me['total_points'], 'position': me['position'], 'of': ranked, 'term': term, 'year': year, 'school': get_settings(), 'bands': bands_json()}

# ---- users, approvals, audit ----
@bp.get('/users')
@login_required('Admin')
def users():
    st = request.args.get('status', 'active')
    return {'rows': [dict(public(r), created_at=r['created_at']) for r in q('SELECT * FROM users WHERE status=%s ORDER BY id', (st,))]}
@bp.post('/users/<int:uid>/<action>')
@login_required('Admin')
def user_action(uid, action):
    u = one('SELECT * FROM users WHERE id=%s', (uid,))
    if not u or uid == g.user['id']: raise ApiError('Not allowed', 400)
    if action == 'approve': ex("UPDATE users SET status='active' WHERE id=%s", (uid,)); log('Approved', u['name']); return {'ok': True}
    if action in ('reject', 'delete'): ex('DELETE FROM users WHERE id=%s', (uid,)); log('Rejected' if action == 'reject' else 'Deleted user', u['name']); return {'ok': True}
    if action == 'reset-password':
        t = secrets.token_urlsafe(9); ex('UPDATE users SET pw_hash=%s WHERE id=%s', (generate_password_hash(t), uid)); log('Password reset by admin', u['name']); return {'temp': t}
    raise ApiError('Unknown action', 404)
@bp.get('/audit')
@login_required('Admin')
def audit_log():
    return {'rows': q('SELECT at, actor_name, actor_role, action, details, ip FROM audit_log ORDER BY id DESC LIMIT 300')}
