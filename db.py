import pymysql, pymysql.cursors
from flask import g
from config import cfg, cfg_int

def connect():
    return pymysql.connect(host=cfg('DB_HOST', '127.0.0.1'), port=cfg_int('DB_PORT', 3306), user=cfg('DB_USER', 'sms'),
                           password=cfg('DB_PASSWORD', ''), database=cfg('DB_NAME', 'sms'), charset='utf8mb4',
                           cursorclass=pymysql.cursors.DictCursor, autocommit=False)
def conn():
    if 'db' not in g: g.db = connect()
    return g.db
def q(sql, a=()):
    with conn().cursor() as c: c.execute(sql, a); return c.fetchall()
def one(sql, a=()):
    r = q(sql, a); return r[0] if r else None
def ex(sql, a=()):
    with conn().cursor() as c: c.execute(sql, a); return c.lastrowid
def many(sql, rows):
    if rows:
        with conn().cursor() as c: c.executemany(sql, rows)
def commit(): conn().commit()
def close(_=None):
    d = g.pop('db', None)
    if d is not None:
        try: d.rollback()
        finally: d.close()
