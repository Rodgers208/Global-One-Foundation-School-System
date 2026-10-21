import re
from datetime import date, datetime
from flask import Blueprint, request, g
from audit import log
from auth import login_required, body
from db import q, one, ex
from errors import ApiError
from settings import fee_year

bp = Blueprint('students', __name__)
ADM_RE = r'^G1S/[0-9]{4}/[0-9]{4,}$'
COLS = 's.id,s.adm,s.name,s.class_id,c.name AS class_name,s.guardian,s.phone,s.gender,s.dob,s.birth_cert,s.status,s.caregiver,s.relationship,s.parent_status'
FROM = 'FROM students s JOIN classes c ON c.id=s.class_id'
def norm(s): return re.sub(r'\s+', '', str(s)).lower()

def next_adm():
    """Next G1S/YYYY/NNNN. The counter row is locked (FOR UPDATE) until the surrounding transaction commits,
    so simultaneous registrations queue up and can never receive the same number."""
    c = one('SELECT n FROM admission_counter WHERE id=1 FOR UPDATE')['n']
    mx = one("SELECT COALESCE(MAX(CAST(SUBSTRING_INDEX(adm,'/',-1) AS UNSIGNED)),0) m FROM students WHERE adm REGEXP %s", (ADM_RE,))['m']
    n = max(c, int(mx)) + 1
    ex('UPDATE admission_counter SET n=%s WHERE id=1', (n,))
    return f'G1S/{date.today().year}/{n:04d}'

def clean(b, classes, create=False):
    """Validate one student. classes = {normalised name: (id, name)}. Raises ValueError with a readable message."""
    name = re.sub(r'\s+', ' ', str(b.get('name') or '')).strip()
    if not 2 <= len(name) <= 100 or not re.fullmatch(r"[^\W\d_](?:[^\W\d_]|[\s'.\-])*", name): raise ValueError('Enter a valid student name')
    cid = cname = None
    if b.get('class_id'):
        hit = next((v for v in classes.values() if v[0] == int(b['class_id'])), None)
    else:
        cname = re.sub(r'\s+', ' ', str(b.get('cls') or '')).strip(); hit = classes.get(norm(cname))
    if hit: cid, cname = hit
    elif not (create and cname and re.fullmatch(r'[A-Za-z0-9 .\-]{2,30}', cname)): raise ValueError(f"Unknown class '{cname or ''}'")
    guardian = str(b.get('guardian') or '').strip(); phone = re.sub(r'\D', '', str(b.get('phone') or ''))
    if len(guardian) > 100: raise ValueError('Guardian name is too long')
    if phone and not 9 <= len(phone) <= 15: raise ValueError('Phone must have 9 to 15 digits')
    g_ = str(b.get('gender') or '').strip().capitalize()
    if g_ not in ('', 'Male', 'Female'): raise ValueError('Gender must be Male or Female')
    dob = str(b.get('dob') or '').strip()[:10] or None
    if dob:
        try: d = datetime.strptime(dob, '%Y-%m-%d').date()
        except ValueError: raise ValueError('Invalid date of birth')
        if d < date(1990, 1, 1) or d > date.today(): raise ValueError('Invalid date of birth')
    out = {'name': name, 'class_id': cid, 'class_name': cname, 'guardian': guardian, 'phone': phone, 'gender': g_, 'dob': dob}
    for k, m in (('birth_cert', 40), ('status', 20), ('caregiver', 30), ('relationship', 40), ('parent_status', 30)):
        v = str(b.get(k) or '').strip()
        if len(v) > m: raise ValueError(f'{k} is too long')
        out[k] = v
    out['status'] = out['status'] or 'Active'
    return out

def class_map(): return {norm(r['name']): (r['id'], r['name']) for r in q('SELECT id, name FROM classes')}
def dup_of(v): return one('SELECT id FROM students WHERE LOWER(name)=%s AND class_id=%s AND phone=%s', (v['name'].lower(), v['class_id'], v['phone']))
INS = 'INSERT INTO students (adm,name,class_id,guardian,phone,gender,dob,birth_cert,status,caregiver,relationship,parent_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'
def insert(v):
    adm = next_adm()
    sid = ex(INS, (adm, v['name'], v['class_id'], v['guardian'], v['phone'], v['gender'], v['dob'], v['birth_cert'], v['status'], v['caregiver'], v['relationship'], v['parent_status']))
    return sid, adm
def get_student(sid): return one(f'SELECT {COLS} {FROM} WHERE s.id=%s', (sid,))
def own_or_staff(sid):
    u = g.user
    if u['role'] == 'Parent' and u['student_id'] != sid: raise ApiError('Not allowed', 403)
    if u['role'] == 'Parent': return
    if u['role'] not in ('Admin', 'Teacher', 'Staff'): raise ApiError('Not allowed', 403)

@bp.get('/students')
@login_required('Admin', 'Teacher', 'Staff')
def list_students():
    term = request.args.get('q', '').strip(); cid = request.args.get('class_id', type=int)
    page = max(request.args.get('page', 1, type=int), 1); size = min(max(request.args.get('size', 50, type=int), 1), 500)
    w, a = [], []
    if term:
        like = f'%{term}%'; w.append('(s.name LIKE %s OR s.adm LIKE %s OR s.phone LIKE %s OR s.guardian LIKE %s)'); a += [like] * 4
    if cid: w.append('s.class_id=%s'); a.append(cid)
    where = ('WHERE ' + ' AND '.join(w)) if w else ''
    total = one(f'SELECT COUNT(*) n {FROM} {where}', a)['n']
    rows = q(f'SELECT {COLS} {FROM} {where} ORDER BY s.id LIMIT %s OFFSET %s', a + [size, (page - 1) * size])
    return {'rows': rows, 'total': total, 'page': page, 'size': size}

@bp.get('/students/<int:sid>')
@login_required()
def profile(sid):
    own_or_staff(sid); s = get_student(sid)
    if not s: raise ApiError('Student not found', 404)
    from finance import balance_of
    s.update(balance_of(sid)); return s

@bp.post('/students')
@login_required('Admin')
def register():
    b = body()
    try: v = clean(b, class_map())
    except ValueError as e: raise ApiError(str(e))
    if not b.get('force') and dup_of(v): raise ApiError('A student with the same name, class and phone already exists', 409, dup=True)
    sid, adm = insert(v); log('Student registered', adm)
    return {'student': get_student(sid)}, 201

@bp.put('/students/<int:sid>')
@login_required('Admin')
def update(sid):
    if not get_student(sid): raise ApiError('Student not found', 404)
    try: v = clean(body(), class_map())
    except ValueError as e: raise ApiError(str(e))
    ex('UPDATE students SET name=%s,class_id=%s,guardian=%s,phone=%s,gender=%s,dob=%s,birth_cert=%s,status=%s,caregiver=%s,relationship=%s,parent_status=%s WHERE id=%s',
       (v['name'], v['class_id'], v['guardian'], v['phone'], v['gender'], v['dob'], v['birth_cert'], v['status'], v['caregiver'], v['relationship'], v['parent_status'], sid))  # adm is never updated
    return {'student': get_student(sid)}

@bp.delete('/students/<int:sid>')
@login_required('Admin')
def delete(sid):
    s = get_student(sid)
    if not s: raise ApiError('Student not found', 404)
    if one('SELECT 1 FROM payments WHERE student_id=%s LIMIT 1', (sid,)) or one('SELECT 1 FROM results WHERE student_id=%s LIMIT 1', (sid,)):
        raise ApiError('This student has payment or result records. Set the status to Inactive instead of deleting.', 409)
    ex('DELETE FROM students WHERE id=%s', (sid,)); log('Student deleted', s['adm'] or s['name']); return {'ok': True}

@bp.get('/students/assign/preview')
@login_required('Admin')
def assign_preview():
    rows = q(f"SELECT {COLS} {FROM} ORDER BY s.id")
    eligible = [r for r in rows if not (r['adm'] and re.fullmatch(ADM_RE[1:-1], r['adm']))]
    seen, dups = {}, set()
    for r in rows:
        k = (r['name'].strip().lower(), r['class_id'], r['phone'])
        if k in seen: dups.update([r['id'], seen[k]])
        else: seen[k] = r['id']
    c = one('SELECT n FROM admission_counter WHERE id=1')['n']
    mx = int(one("SELECT COALESCE(MAX(CAST(SUBSTRING_INDEX(adm,'/',-1) AS UNSIGNED)),0) m FROM students WHERE adm REGEXP %s", (ADM_RE,))['m'])
    n = max(c, mx); y = date.today().year; items = []
    for r in eligible:
        n += 1; items.append({'id': r['id'], 'name': r['name'], 'class_name': r['class_name'], 'guardian': r['guardian'], 'current': r['adm'] or '', 'proposed': f'G1S/{y}/{n:04d}', 'dup': r['id'] in dups})
    return {'total': len(rows), 'valid': len(rows) - len(eligible), 'items': items}

@bp.post('/students/assign')
@login_required('Admin')
def assign():
    ids = sorted({int(i) for i in (body().get('ids') or []) if str(i).isdigit()})
    if not ids or len(ids) > 5000: raise ApiError('Select students first')
    done = skipped = 0
    for sid in ids:
        s = one('SELECT id, adm FROM students WHERE id=%s FOR UPDATE', (sid,))
        if not s or (s['adm'] and re.fullmatch(ADM_RE[1:-1], s['adm'])): skipped += 1; continue
        ex('UPDATE students SET adm=%s WHERE id=%s', (next_adm(), sid)); done += 1   # never touches an already valid number
    log('Bulk admission numbers', f'{done} assigned'); return {'assigned': done, 'skipped': skipped}

@bp.post('/students/import')
@login_required('Admin')
def import_students():
    b = body(); rows = b.get('rows')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5000: raise ApiError('Provide between 1 and 5000 rows')
    classes = class_map(); new_classes, seen, out = [], set(), []
    for i, x in enumerate(rows):
        try: v = clean(x if isinstance(x, dict) else {}, classes, create=True)
        except ValueError as e: out.append({'row': i + 2, 'name': (x or {}).get('name', ''), 'class_name': (x or {}).get('cls', ''), 'phone': (x or {}).get('phone', ''), 'result': 'invalid', 'reason': str(e)}); continue
        if v['class_id'] is None:
            new_classes.append(v['class_name']); classes[norm(v['class_name'])] = (-len(new_classes), v['class_name']); v['class_id'] = -len(new_classes)
        key = (v['name'].lower(), v['class_id'], v['phone'])
        real = v['class_id'] > 0 and dup_of(v)
        if key in seen or real: out.append({**v, 'row': i + 2, 'result': 'duplicate', 'reason': 'Student already exists'}); continue
        seen.add(key); out.append({**v, 'row': i + 2, 'result': 'new'})
    cnt = lambda s: sum(1 for r in out if r['result'] == s)
    if not b.get('commit'): return {'rows': out[:300], 'new': cnt('new'), 'duplicate': cnt('duplicate'), 'invalid': cnt('invalid'), 'newClasses': new_classes}
    order = q('SELECT COALESCE(MAX(sort_order),0) m FROM classes')[0]['m']; created = {}
    for k, nm in enumerate(new_classes, 1): created[-k] = ex('INSERT INTO classes (name, sort_order) VALUES (%s,%s)', (nm, order + k))
    made = 0
    for r in out:
        if r['result'] != 'new': continue
        r['class_id'] = created.get(r['class_id'], r['class_id']); insert(r); made += 1
    log('Bulk import', f'{made} students'); return {'created': made, 'skipped': len(out) - made}

@bp.get('/students/me')
@login_required('Parent')
def my_child():
    if not g.user['student_id']: raise ApiError('No student is linked to this account', 404)
    return profile(g.user['student_id'])
