import datetime, decimal, os, logging
from flask import Flask, jsonify, send_from_directory, request
from flask.json.provider import DefaultJSONProvider
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError: pass
import db
from config import cfg
from errors import ApiError

class Provider(DefaultJSONProvider):
    @staticmethod
    def default(o):
        if isinstance(o, (datetime.datetime, datetime.date)): return o.isoformat()
        if isinstance(o, decimal.Decimal): return int(o) if o == o.to_integral_value() else float(o)
        return DefaultJSONProvider.default(o)

def create_app():
    dist = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend', 'dist'))
    app = Flask(__name__, static_folder=None); app.json = Provider(app)
    app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024
    if cfg('TRUST_PROXY') == '1': app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    from auth import bp as auth_bp; from students import bp as st_bp; from finance import bp as fin_bp; from school import bp as sch_bp
    for b in (auth_bp, st_bp, fin_bp, sch_bp): app.register_blueprint(b, url_prefix='/api')
    app.teardown_request(db.close)

    @app.errorhandler(ApiError)
    def api_error(e):
        if e.keep:
            try: db.commit()
            except Exception: pass
        return jsonify({'err': e.msg, **e.extra}), e.status
    @app.errorhandler(HTTPException)
    def http_error(e):
        if request.path.startswith('/api/'): return jsonify({'err': e.description}), e.code
        return e
    @app.errorhandler(Exception)
    def crash(e):
        logging.exception('Unhandled error'); return jsonify({'err': 'Something went wrong. Nothing was saved.'}), 500
    @app.after_request
    def finish(r):
        if r.status_code < 400 and request.path.startswith('/api/'):
            try: db.commit()
            except Exception:
                logging.exception('Commit failed'); db.close(); return jsonify({'err': 'Could not save your changes. Nothing was saved.'}), 500
        r.headers.update({'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin'})
        if request.path.startswith('/api/'): r.headers['Cache-Control'] = 'no-store'
        return r
    @app.route('/', defaults={'p': ''})
    @app.route('/<path:p>')
    def spa(p):
        if p.startswith('api/'): return jsonify({'err': 'Not found'}), 404
        if p and os.path.isfile(os.path.join(dist, p)): return send_from_directory(dist, p)
        if os.path.isfile(os.path.join(dist, 'index.html')): return send_from_directory(dist, 'index.html')
        return jsonify({'msg': 'API is running. Build the frontend (npm run build) to serve the web app here.'})
    return app

app = create_app() if os.environ.get('JWT_SECRET') else None
if __name__ == '__main__':
    (app or create_app()).run(host='127.0.0.1', port=int(cfg('PORT', 5000)))
