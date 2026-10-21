"""Usage:  python manage.py init     create tables, default classes/subjects, and the first admin account; also upgrades an older database (safe to re-run)
          python manage.py admin-password <Aol#yaWa@8Fahm$* >   reset the first admin's password"""
import os, sys, re
from config import utcnow
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError: pass
from werkzeug.security import generate_password_hash
import db
CLASSES = ['Playgroup', 'PP1', 'PP2'] + [f'Grade {i}' for i in range(1, 10)] + ['Form 3', 'Form 4']
# The nine junior-school learning areas used in the school's exam workbook (Grades 7-9)
SUBJECTS = ['Mathematics', 'English', 'Kiswahili', 'Integrated Science', 'Pre-Technical Studies', 'Creative Arts & Sports', 'Social Studies', 'Religious Education', 'Agriculture']

def run(sql_file, c):
    for stmt in re.split(r';\s*\n', open(sql_file, encoding='utf8').read()):
        stmt = '\n'.join(l for l in stmt.splitlines() if not l.strip().startswith('--')).strip()
        if stmt: c.execute(stmt)

def migrate(c):
    """Safe, repeatable upgrades for databases created by an earlier version. Never deletes data."""
    c.execute("SELECT COUNT(*) n FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='results' AND column_name='exam'")
    if c.fetchone()['n'] == 0:
        c.execute('ALTER TABLE results ADD COLUMN exam TINYINT NOT NULL DEFAULT 1 AFTER term')
        c.execute('ALTER TABLE results DROP PRIMARY KEY, ADD PRIMARY KEY (student_id, subject_id, year, term, exam)')
        c.execute('ALTER TABLE results MODIFY mark DECIMAL(5,2) NOT NULL')
        print('Upgraded results: 3 exams per term and decimal marks. Existing marks were kept as Exam 1.')
    for n in SUBJECTS: c.execute('INSERT IGNORE INTO subjects (name) VALUES (%s)', (n,))   # adds any missing learning areas, removes nothing

def init():
    pw = os.environ.get('ADMIN_PASSWORD'); email = os.environ.get('ADMIN_EMAIL', 'admin@school.com')
    if not pw or len(pw) < 8 or pw.startswith('choose-'): sys.exit('Set your own ADMIN_PASSWORD (at least 8 characters) in backend/.env before running init.')
    conn = db.connect(); c = conn.cursor()
    run(os.path.join(os.path.dirname(__file__), 'schema.sql'), c)
    c.execute('SELECT COUNT(*) n FROM classes')
    if c.fetchone()['n'] == 0:
        for i, n in enumerate(CLASSES, 1): c.execute('INSERT INTO classes (name, sort_order) VALUES (%s,%s)', (n, i))
        for n in SUBJECTS: c.execute('INSERT INTO subjects (name) VALUES (%s)', (n,))
        for n in ('Electronics', 'Books', 'Furniture', 'Stationery'): c.execute('INSERT INTO inventory_categories (name) VALUES (%s)', (n,))
    migrate(c)
    c.execute("SELECT COUNT(*) n FROM users WHERE role='Admin'")
    if c.fetchone()['n'] == 0:
        c.execute("INSERT INTO users (name,email,pw_hash,role,status,created_at) VALUES ('System Admin',%s,%s,'Admin','active',%s)", (email.lower(), generate_password_hash(pw), utcnow()))
        print('Created admin account:', email)
    conn.commit(); conn.close(); print('Database ready.')

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'init': init()
    elif cmd == 'admin-password' and len(sys.argv) > 2 and len(sys.argv[2]) >= 8:
        conn = db.connect(); c = conn.cursor(); c.execute("UPDATE users SET pw_hash=%s WHERE role='Admin' ORDER BY id LIMIT 1", (generate_password_hash(sys.argv[2]),)); conn.commit(); print('Admin password updated.')
    else: print(__doc__)
