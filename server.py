import os
import re
import csv
import io
import json
from datetime import datetime
from urllib.parse import urlparse

from flask import Flask, request, jsonify, session, redirect, send_from_directory, Response
from flask_cors import CORS
import pymysql
from pymysql.cursors import DictCursor

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.getenv('SECRET_KEY', 'smart-ark-dev-secret')
CORS(app, supports_credentials=True)

DEFAULT_SHEET = '活動報到名單'
DEFAULT_ADMIN = 'admin'

# -----------------------------
# DB
# -----------------------------
def db_params():
    url = os.getenv('DATABASE_URL') or os.getenv('MYSQL_URL')
    if url:
        u = urlparse(url)
        return dict(
            host=u.hostname,
            port=u.port or 3306,
            user=u.username,
            password=u.password,
            database=(u.path or '').lstrip('/') or os.getenv('MYSQLDATABASE'),
            charset='utf8mb4',
            cursorclass=DictCursor,
            autocommit=False,
        )
    return dict(
        host=os.getenv('MYSQLHOST') or os.getenv('DB_HOST') or 'localhost',
        port=int(os.getenv('MYSQLPORT') or os.getenv('DB_PORT') or 3306),
        user=os.getenv('MYSQLUSER') or os.getenv('DB_USER') or 'root',
        password=os.getenv('MYSQLPASSWORD') or os.getenv('DB_PASSWORD') or '',
        database=os.getenv('MYSQLDATABASE') or os.getenv('DB_NAME') or 'railway',
        charset='utf8mb4',
        cursorclass=DictCursor,
        autocommit=False,
    )


def get_db_connection():
    return pymysql.connect(**db_params())


def column_exists(cur, table, col):
    cur.execute("""
        SELECT COUNT(*) AS c FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s
    """, (table, col))
    return (cur.fetchone() or {}).get('c', 0) > 0


def add_col(cur, table, col, spec):
    if not column_exists(cur, table, col):
        cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{col}` {spec}")


def ensure_core_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(120) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            allowed_events LONGTEXT,
            current_event VARCHAR(255),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS event_configs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            admin_username VARCHAR(120) NOT NULL DEFAULT 'admin',
            google_sheet_name VARCHAR(255) NOT NULL DEFAULT '活動報到名單',
            event_title VARCHAR(255),
            banner_image_url LONGTEXT,
            map_image_url LONGTEXT,
            show_meal_options TINYINT(1) DEFAULT 1,
            products LONGTEXT,
            industry_mappings LONGTEXT,
            agenda LONGTEXT,
            event_config LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_event_config (admin_username, google_sheet_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS event_registrations (
            id INT AUTO_INCREMENT PRIMARY KEY,
            admin_username VARCHAR(120) NOT NULL DEFAULT 'admin',
            google_sheet_name VARCHAR(255) NOT NULL DEFAULT '活動報到名單',
            name VARCHAR(255),
            phone VARCHAR(100),
            email VARCHAR(255),
            company VARCHAR(255),
            job_title VARCHAR(255),
            seat VARCHAR(100),
            status VARCHAR(40) DEFAULT 'pending',
            meal_choice VARCHAR(80),
            original_meal_choice VARCHAR(80),
            is_original TINYINT(1) DEFAULT 1,
            proxy_name VARCHAR(255),
            proxy_phone VARCHAR(100),
            checked_in_at DATETIME NULL,
            portrait_consent TINYINT(1) NULL,
            portrait_consent_status VARCHAR(40),
            special_notes LONGTEXT,
            raw_data LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_event (admin_username, google_sheet_name),
            KEY idx_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS agenda_items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            admin_username VARCHAR(120) NOT NULL DEFAULT 'admin',
            google_sheet_name VARCHAR(255) NOT NULL DEFAULT '活動報到名單',
            sort_order INT DEFAULT 0,
            time VARCHAR(60),
            event VARCHAR(255),
            title VARCHAR(255),
            description LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            KEY idx_agenda (admin_username, google_sheet_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS industry_mappings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            admin_username VARCHAR(120) NOT NULL DEFAULT 'admin',
            google_sheet_name VARCHAR(255) NOT NULL DEFAULT '活動報到名單',
            company_name VARCHAR(255),
            industry VARCHAR(255),
            sort_order INT DEFAULT 0,
            KEY idx_industry (admin_username, google_sheet_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS exhibitors (
            id INT AUTO_INCREMENT PRIMARY KEY,
            admin_username VARCHAR(120) NOT NULL DEFAULT 'admin',
            google_sheet_name VARCHAR(255) NOT NULL DEFAULT '活動報到名單',
            name VARCHAR(255),
            industry VARCHAR(255),
            image_url LONGTEXT,
            website LONGTEXT,
            contact VARCHAR(255),
            description LONGTEXT,
            sort_order INT DEFAULT 0,
            KEY idx_exhibitor (admin_username, google_sheet_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Safe migrations for older DBs
        cols = {
            'event_configs': {
                'admin_username': "VARCHAR(120) NOT NULL DEFAULT 'admin'",
                'google_sheet_name': "VARCHAR(255) NOT NULL DEFAULT '活動報到名單'",
                'event_title': 'VARCHAR(255)',
                'banner_image_url': 'LONGTEXT',
                'map_image_url': 'LONGTEXT',
                'show_meal_options': 'TINYINT(1) DEFAULT 1',
                'products': 'LONGTEXT',
                'industry_mappings': 'LONGTEXT',
                'agenda': 'LONGTEXT',
                'event_config': 'LONGTEXT',
                'success_card_config': 'LONGTEXT',
                'success_info_cards_config': 'LONGTEXT',
                'dashboard_agenda_config': 'LONGTEXT',
                'theme_background_color': "VARCHAR(20) DEFAULT '#061A18'",
                'theme_primary_color': "VARCHAR(20) DEFAULT '#14B8A6'",
                'theme_accent_color': "VARCHAR(20) DEFAULT '#5EEAD4'",
                'theme_text_color': "VARCHAR(20) DEFAULT '#ECFEFF'",
            },
            'event_registrations': {
                'admin_username': "VARCHAR(120) NOT NULL DEFAULT 'admin'",
                'google_sheet_name': "VARCHAR(255) NOT NULL DEFAULT '活動報到名單'",
                'name': 'VARCHAR(255)', 'phone': 'VARCHAR(100)', 'email': 'VARCHAR(255)',
                'company': 'VARCHAR(255)', 'job_title': 'VARCHAR(255)', 'seat': 'VARCHAR(100)',
                'status': "VARCHAR(40) DEFAULT 'pending'", 'meal_choice': 'VARCHAR(80)',
                'original_meal_choice': 'VARCHAR(80)', 'is_original': 'TINYINT(1) DEFAULT 1',
                'proxy_name': 'VARCHAR(255)', 'proxy_phone': 'VARCHAR(100)',
                'checked_in_at': 'DATETIME NULL', 'portrait_consent': 'TINYINT(1) NULL',
                'portrait_consent_status': 'VARCHAR(40)', 'special_notes': 'LONGTEXT', 'raw_data': 'LONGTEXT'
            }
        }
        for table, items in cols.items():
            for col, spec in items.items():
                add_col(cur, table, col, spec)

        cur.execute("SELECT id FROM admins WHERE username=%s", (DEFAULT_ADMIN,))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO admins (username, password, allowed_events, current_event)
                VALUES (%s,%s,%s,%s)
            """, (DEFAULT_ADMIN, 'admin123', DEFAULT_SHEET, DEFAULT_SHEET))
    conn.commit()


def event_args():
    admin = (request.args.get('admin') or request.json.get('admin') if request.is_json and isinstance(request.json, dict) else None) or session.get('username') or DEFAULT_ADMIN
    sheet = (request.args.get('sheet') or request.args.get('google_sheet_name') or (request.json.get('sheet') if request.is_json and isinstance(request.json, dict) else None) or session.get('current_admin_sheet') or DEFAULT_SHEET)
    return str(admin).strip() or DEFAULT_ADMIN, str(sheet).strip() or DEFAULT_SHEET


def q_event():
    admin, sheet = event_args()
    return admin, sheet


def json_loads(v, fallback):
    if v is None or v == '':
        return fallback
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return fallback


def get_config(conn, admin, sheet):
    ensure_config(conn, admin, sheet)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM event_configs WHERE admin_username=%s AND google_sheet_name=%s LIMIT 1", (admin, sheet))
        row = cur.fetchone() or {}
    cfg = dict(row)
    cfg['products'] = json_loads(cfg.get('products'), [])
    cfg['industry_mappings'] = json_loads(cfg.get('industry_mappings'), [])
    cfg['agenda'] = json_loads(cfg.get('agenda'), [])
    extra = json_loads(cfg.get('event_config'), {})
    if isinstance(extra, dict):
        cfg.update(extra)
    cfg['google_sheet_name'] = sheet
    cfg['admin_username'] = admin
    return cfg


def ensure_config(conn, admin, sheet):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO event_configs (admin_username, google_sheet_name, event_title, products, industry_mappings, agenda, event_config)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE updated_at=CURRENT_TIMESTAMP
        """, (admin, sheet, sheet, '[]', '[]', '[]', '{}'))
    conn.commit()


def save_config(conn, admin, sheet, payload):
    ensure_config(conn, admin, sheet)
    payload = payload or {}
    products = payload.get('products')
    industry = payload.get('industry_mappings')
    agenda = payload.get('agenda')

    known = {
        'event_title', 'banner_image_url', 'map_image_url', 'show_meal_options',
        'success_card_config', 'success_info_cards_config', 'dashboard_agenda_config',
        'theme_background_color', 'theme_primary_color', 'theme_accent_color', 'theme_text_color'
    }
    extra = {k: v for k, v in payload.items() if k not in known and k not in {'products','industry_mappings','agenda','admin','sheet','google_sheet_name'}}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT event_config FROM event_configs WHERE admin_username=%s AND google_sheet_name=%s
        """, (admin, sheet))
        old_extra = json_loads((cur.fetchone() or {}).get('event_config'), {})
        if isinstance(old_extra, dict):
            old_extra.update(extra)
        else:
            old_extra = extra

        updates = {
            'event_title': payload.get('event_title') or payload.get('google_sheet_name') or sheet,
            'banner_image_url': payload.get('banner_image_url'),
            'map_image_url': payload.get('map_image_url'),
            'show_meal_options': 1 if payload.get('show_meal_options', True) else 0,
            'products': json.dumps(products, ensure_ascii=False) if products is not None else None,
            'industry_mappings': json.dumps(industry, ensure_ascii=False) if industry is not None else None,
            'agenda': json.dumps(agenda, ensure_ascii=False) if agenda is not None else None,
            'event_config': json.dumps(old_extra, ensure_ascii=False)
        }
        # include only columns that exist and value not None, so partial save won't erase other settings
        set_parts, vals = [], []
        for k, v in updates.items():
            if v is not None and column_exists(cur, 'event_configs', k):
                set_parts.append(f"`{k}`=%s")
                vals.append(v)
        for k in ['success_card_config','success_info_cards_config','dashboard_agenda_config','theme_background_color','theme_primary_color','theme_accent_color','theme_text_color']:
            if k in payload and column_exists(cur, 'event_configs', k):
                set_parts.append(f"`{k}`=%s")
                vals.append(payload[k])
        if set_parts:
            vals += [admin, sheet]
            cur.execute(f"UPDATE event_configs SET {', '.join(set_parts)} WHERE admin_username=%s AND google_sheet_name=%s", vals)
    conn.commit()


def pick(row, keys):
    for k in keys:
        if k in row and str(row[k]).strip():
            return str(row[k]).strip()
    return ''


def normalize_registration(row):
    return {
        'name': pick(row, ['姓名','name','Name']),
        'phone': pick(row, ['手機','電話','phone','Phone']),
        'email': pick(row, ['Email','email','電子郵件']),
        'company': pick(row, ['公司','company','Company']),
        'job_title': pick(row, ['職稱','職位','title','job_title']),
        'seat': pick(row, ['桌號','座位','桌次','seat','Seat']),
        'raw_data': json.dumps(row, ensure_ascii=False)
    }


def status_checked(status):
    return str(status or '').lower() in ['checked_in', '已報到', '替代', 'done']


def public_user(row):
    r = dict(row or {})

    for k in ('meal', 'meal_choice', 'meal_preference', 'original_meal_choice'):
        r.pop(k, None)

    r['job_title'] = r.get('job_title') or ''
    r['portrait_consent_status'] = r.get('portrait_consent_status') or ''
    return r

# -----------------------------
# Pages
# -----------------------------
@app.route('/')
def index():
    return send_from_directory('.', '活動報到系統.html')

@app.route('/admin')
def admin_page():
    return send_from_directory('.', 'admin.html')

@app.route('/dashboard')
def dashboard_page():
    return send_from_directory('.', 'dashboard.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('.', path)

# -----------------------------
# Auth / bootstrap
# -----------------------------
@app.route('/api/bootstrap_db')
def bootstrap_db():
    conn = None
    try:
        conn = get_db_connection()
        ensure_core_tables(conn)
        return jsonify(success=True, message='DB migration completed')
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn: conn.close()

@app.route('/api/login', methods=['POST'])
def api_login():
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        u, p = (data.get('username') or '').strip(), (data.get('password') or '').strip()
        conn = get_db_connection(); ensure_core_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM admins WHERE TRIM(username)=%s AND TRIM(password)=%s LIMIT 1", (u, p))
            admin = cur.fetchone()
        if not admin:
            return jsonify(success=False, message='帳密錯誤'), 401
        allowed = [x.strip() for x in (admin.get('allowed_events') or DEFAULT_SHEET).split(',') if x.strip()] or [DEFAULT_SHEET]
        current = admin.get('current_event') or allowed[0]
        session['admin_logged_in'] = True
        session['username'] = admin['username']
        session['allowed_sheets'] = allowed
        session['current_admin_sheet'] = current
        return jsonify(success=True, username=admin['username'], allowed_sheets=allowed, current_sheet=current)
    except Exception as e:
        return jsonify(success=False, message=f'登入 API 失敗：{e}'), 500
    finally:
        if conn: conn.close()

@app.route('/api/register', methods=['POST'])
def api_register():
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        username = (data.get('username') or '').strip()
        password = (data.get('password') or '').strip()
        allowed = (data.get('allowed_events') or DEFAULT_SHEET).strip()
        code = (data.get('invite_code') or '').strip()
        required = os.getenv('REGISTER_INVITE_CODE', '').strip()
        if required and code != required:
            return jsonify(success=False, message='邀請碼錯誤'), 403
        if len(username) < 3 or len(password) < 6:
            return jsonify(success=False, message='帳號至少 3 字元、密碼至少 6 碼'), 400
        conn = get_db_connection(); ensure_core_tables(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO admins (username,password,allowed_events,current_event) VALUES (%s,%s,%s,%s)", (username, password, allowed, allowed.split(',')[0].strip() or DEFAULT_SHEET))
        conn.commit()
        return jsonify(success=True, message='管理員建立成功')
    except pymysql.err.IntegrityError:
        return jsonify(success=False, message='帳號已存在'), 409
    except Exception as e:
        if conn: conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn: conn.close()

@app.route('/api/logout')
def logout():
    session.clear()
    return redirect('/login.html')

@app.route('/api/user/info')
def user_info():
    username = session.get('username') or request.args.get('admin') or DEFAULT_ADMIN
    return jsonify(success=True, username=username)

@app.route('/api/debug_login')
def debug_login():
    conn = get_db_connection(); ensure_core_tables(conn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username, LENGTH(password) password_len, allowed_events, current_event FROM admins")
            return jsonify(success=True, admins=cur.fetchall())
    finally:
        conn.close()

# -----------------------------
# Config / sheets
# -----------------------------
@app.route('/api/current_sheet')
def current_sheet():
    admin = request.args.get('admin') or session.get('username') or DEFAULT_ADMIN
    return jsonify(success=True, current_sheet=session.get('current_admin_sheet') or DEFAULT_SHEET, admin=admin)

@app.route('/api/session/sheet', methods=['POST'])
def session_sheet():
    data = request.get_json(silent=True) or {}
    sheet = (data.get('sheet') or DEFAULT_SHEET).strip()
    admin = (data.get('admin') or session.get('username') or DEFAULT_ADMIN).strip()
    session['current_admin_sheet'] = sheet
    session['username'] = admin
    conn = get_db_connection(); ensure_core_tables(conn)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE admins SET current_event=%s WHERE username=%s", (sheet, admin))
        conn.commit()
        ensure_config(conn, admin, sheet)
        return jsonify(success=True, current_sheet=sheet)
    finally:
        conn.close()

@app.route('/api/sheets/list')
def sheets_list():
    admin = session.get('username') or request.args.get('admin') or DEFAULT_ADMIN
    conn = get_db_connection(); ensure_core_tables(conn)
    try:
        sheets = []
        with conn.cursor() as cur:
            cur.execute("SELECT allowed_events,current_event FROM admins WHERE username=%s", (admin,))
            a = cur.fetchone()
            if a and a.get('allowed_events'):
                sheets += [x.strip() for x in a['allowed_events'].split(',') if x.strip()]
            cur.execute("SELECT DISTINCT google_sheet_name FROM event_configs WHERE admin_username=%s", (admin,))
            sheets += [r['google_sheet_name'] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT google_sheet_name FROM event_registrations WHERE admin_username=%s", (admin,))
            sheets += [r['google_sheet_name'] for r in cur.fetchall()]
        sheets = list(dict.fromkeys(sheets or [DEFAULT_SHEET]))
        return jsonify(success=True, sheets=sheets)
    finally:
        conn.close()

@app.route('/api/config', methods=['GET','POST'])
def api_config():
    conn = None
    try:
        conn = get_db_connection(); ensure_core_tables(conn)
        if request.method == 'GET':
            admin, sheet = q_event()
            cfg = get_config(conn, admin, sheet)
            return jsonify(cfg)
        payload = request.get_json(silent=True) or {}
        admin = payload.get('admin_username') or payload.get('admin') or session.get('username') or DEFAULT_ADMIN
        sheet = payload.get('google_sheet_name') or payload.get('sheet') or session.get('current_admin_sheet') or DEFAULT_SHEET
        save_config(conn, admin, sheet, payload)
        return jsonify(success=True)
    except Exception as e:
        if conn: conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn: conn.close()

@app.route('/api/event-config', methods=['GET'])
def event_config_get():
    conn = get_db_connection(); ensure_core_tables(conn)
    try:
        admin, sheet = q_event()
        cfg = get_config(conn, admin, sheet)
        return jsonify(success=True, config=cfg)
    finally:
        conn.close()

@app.route('/api/admin/event-config', methods=['POST','PUT'])
def event_config_save():
    conn = None
    try:
        conn = get_db_connection(); ensure_core_tables(conn)
        admin, sheet = q_event()
        payload = request.get_json(silent=True) or {}
        payload.update({
            'theme_background_color': payload.get('theme_background_color', '#061A18'),
            'theme_primary_color': payload.get('theme_primary_color', '#14B8A6'),
            'theme_accent_color': payload.get('theme_accent_color', '#5EEAD4'),
            'theme_text_color': payload.get('theme_text_color', '#ECFEFF'),
        })
        save_config(conn, admin, sheet, payload)
        return jsonify(success=True)
    except Exception as e:
        if conn: conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn: conn.close()

# -----------------------------
# CSV import/export
# -----------------------------
@app.route('/api/sheets/upload_csv', methods=['POST'])
def upload_csv():
    conn = None
    try:
        f = request.files.get('file')
        if not f:
            return jsonify(success=False, message='沒有收到 CSV 檔案'), 400
        raw = f.read()
        text = None
        for enc in ['utf-8-sig','utf-8','cp950','big5']:
            try:
                text = raw.decode(enc); break
            except Exception: pass
        if text is None:
            return jsonify(success=False, message='CSV 編碼無法讀取'), 400
        rows = list(csv.DictReader(io.StringIO(text)))
        sheet = os.path.splitext(f.filename or DEFAULT_SHEET)[0] or DEFAULT_SHEET
        admin = session.get('username') or DEFAULT_ADMIN
        conn = get_db_connection(); ensure_core_tables(conn)
        ensure_config(conn, admin, sheet)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s", (admin, sheet))
            for raw_row in rows:
                r = normalize_registration(raw_row)
                cur.execute("""
                    INSERT INTO event_registrations
                    (admin_username,google_sheet_name,name,phone,email,company,job_title,seat,status,meal_choice,original_meal_choice,special_notes,raw_data)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s)
                """, (admin, sheet, r['name'], r['phone'], r['email'], r['company'], r['job_title'], r['seat'], r['meal_choice'], r['meal_choice'], r['special_notes'], r['raw_data']))
            # add sheet to admin allowed events, preserving existing
            cur.execute("SELECT allowed_events FROM admins WHERE username=%s", (admin,))
            a = cur.fetchone()
            allowed = [x.strip() for x in ((a or {}).get('allowed_events') or '').split(',') if x.strip()]
            if sheet not in allowed: allowed.append(sheet)
            cur.execute("UPDATE admins SET allowed_events=%s, current_event=%s WHERE username=%s", (','.join(allowed or [sheet]), sheet, admin))
        conn.commit()
        session['current_admin_sheet'] = sheet
        return jsonify(success=True, message=f'已匯入 {len(rows)} 筆名單到「{sheet}」', sheet=sheet)
    except Exception as e:
        if conn: conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn: conn.close()

@app.route('/api/sheets/export_csv')
def export_csv():
    admin, sheet = q_event()
    conn = get_db_connection(); ensure_core_tables(conn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name,phone,email,company,job_title,seat,status,meal_choice,portrait_consent_status,checked_in_at FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s ORDER BY id", (admin, sheet))
            rows = cur.fetchall()
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(['姓名','手機','Email','公司','職稱','桌號','狀態','餐食','肖像權','報到時間'])
        for r in rows:
            w.writerow([r.get('name'), r.get('phone'), r.get('email'), r.get('company'), r.get('job_title'), r.get('seat'), r.get('status'), r.get('meal_choice'), r.get('portrait_consent_status'), r.get('checked_in_at')])
        data = '\ufeff' + out.getvalue()
        return Response(data, mimetype='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{sheet}.csv"'})
    finally:
        conn.close()

# -----------------------------
# Search / checkin
# -----------------------------
@app.route('/api/search/<method>')
def search(method):
    admin, sheet = q_event()
    conn = get_db_connection(); ensure_core_tables(conn)
    try:
        val = request.args.get(method) or request.args.get('name') or request.args.get('q') or ''
        val = val.strip()
        field = {'name':'name','phone':'phone','email':'email'}.get(method)
        with conn.cursor() as cur:
            if method == 'company':
                cur.execute("SELECT DISTINCT company FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s AND company LIKE %s AND company<>'' ORDER BY company LIMIT 50", (admin, sheet, f'%{val}%'))
                companies = [{'company': r['company'], 'name': r['company']} for r in cur.fetchall()]
                return jsonify(success=True, data=companies)
            elif field:
                cur.execute(f"SELECT * FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s AND `{field}` LIKE %s ORDER BY id LIMIT 50", (admin, sheet, f'%{val}%'))
                return jsonify(success=True, data=[public_user(r) for r in cur.fetchall()])
        return jsonify(success=True, data=[])
    finally:
        conn.close()

@app.route('/api/search/company_members')
def company_members():
    admin, sheet = q_event()
    name = (request.args.get('name') or '').strip()
    conn = get_db_connection(); ensure_core_tables(conn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s AND company=%s ORDER BY id", (admin, sheet, name))
            return jsonify(success=True, data=[public_user(r) for r in cur.fetchall()])
    finally:
        conn.close()

@app.route('/api/checkin/<int:rid>', methods=['POST'])
def checkin(rid):
    conn = None
    try:
        admin, sheet = q_event()
        data = request.get_json(silent=True) or {}
        conn = get_db_connection(); ensure_core_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM event_registrations WHERE id=%s AND admin_username=%s AND google_sheet_name=%s", (rid, admin, sheet))
            user = cur.fetchone()
            if not user:
                return jsonify(success=False, message='找不到此旅客'), 404
            if status_checked(user.get('status')):
                return jsonify(success=False, error='already_done', message='已報到'), 409
            meal_choice = data.get('meal') or data.get('meal_preference') or user.get('meal_choice') or ''
            is_original = 1 if data.get('is_original', True) else 0
            proxy = data.get('proxy_info') or {}
            portrait_bool = data.get('portrait_consent')
            portrait_status = data.get('portrait_consent_status') or data.get('image_rights_status') or ('同意' if portrait_bool else '不同意')
            cur.execute("""
                UPDATE event_registrations
                SET status=%s, meal_choice=%s, is_original=%s, proxy_name=%s, proxy_phone=%s,
                    checked_in_at=NOW(), portrait_consent=%s, portrait_consent_status=%s
                WHERE id=%s
            """, ('checked_in' if is_original else '替代', meal_choice, is_original, proxy.get('name',''), proxy.get('phone',''), 1 if portrait_bool else 0, portrait_status, rid))
            cur.execute("SELECT * FROM event_registrations WHERE id=%s", (rid,))
            updated = public_user(cur.fetchone())
        conn.commit()
        return jsonify(success=True, data=updated)
    except Exception as e:
        if conn: conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn: conn.close()

@app.route('/api/registrations/add', methods=['POST'])
def registration_add():
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        admin = data.get('admin') or session.get('username') or request.args.get('admin') or DEFAULT_ADMIN
        sheet = data.get('sheet') or session.get('current_admin_sheet') or request.args.get('sheet') or DEFAULT_SHEET
        meal = data.get('meal') or data.get('meal_preference') or data.get('meal_choice') or ''
        portrait_bool = data.get('portrait_consent')
        portrait_status = data.get('portrait_consent_status') or ('同意' if portrait_bool else '不同意')
        conn = get_db_connection(); ensure_core_tables(conn); ensure_config(conn, admin, sheet)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO event_registrations
                (admin_username,google_sheet_name,name,phone,email,company,job_title,seat,status,meal_choice,original_meal_choice,checked_in_at,portrait_consent,portrait_consent_status,raw_data)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'checked_in',%s,%s,NOW(),%s,%s,%s)
            """, (admin, sheet, data.get('name',''), data.get('phone',''), data.get('email',''), data.get('company',''), data.get('job_title',''), data.get('seat','現場安排'), meal, meal, 1 if portrait_bool else 0, portrait_status, json.dumps(data, ensure_ascii=False)))
            rid = cur.lastrowid
        conn.commit()
        return jsonify(success=True, id=rid)
    except Exception as e:
        if conn: conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn: conn.close()

# -----------------------------
# Stats
# -----------------------------
def get_logs(conn, admin, sheet, limit=None):
    sql = "SELECT * FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s"
    if limit:
        sql += " AND status IN ('checked_in','已報到','替代','done') ORDER BY checked_in_at DESC, id DESC LIMIT %s"
        args = (admin, sheet, limit)
    else:
        sql += " ORDER BY id"
        args = (admin, sheet)
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return [public_user(r) for r in cur.fetchall()]

@app.route('/api/dashboard_stats')
def dashboard_stats():
    admin, sheet = q_event()
    conn = get_db_connection(); ensure_core_tables(conn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) total, SUM(CASE WHEN status IN ('checked_in','已報到','替代','done') THEN 1 ELSE 0 END) checked FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s", (admin, sheet))
            s = cur.fetchone() or {}
            total, checked = int(s.get('total') or 0), int(s.get('checked') or 0)
            cur.execute("SELECT seat, COUNT(*) total, SUM(CASE WHEN status IN ('checked_in','已報到','替代','done') THEN 1 ELSE 0 END) checked_in FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s GROUP BY seat ORDER BY seat", (admin, sheet))
            table_stats = cur.fetchall()
        logs = get_logs(conn, admin, sheet, 25)
        return jsonify(success=True, stats={
            'total': total,
            'checked_in': checked,
            'not_checked_in': max(total-checked, 0),
            'logs': logs,
            'table_stats': table_stats,
        })
    finally:
        conn.close()

@app.route('/api/stats/meals')
def meal_stats():
    admin, sheet = q_event()
    conn = get_db_connection(); ensure_core_tables(conn)
    try:
        logs = get_logs(conn, admin, sheet, None)
        meals = {'葷食':0, '素食':0, '不需餐食/其他':0}
        special_notes = []
        for r in logs:
            m = str(r.get('meal_choice') or r.get('meal') or '').strip()
            if '葷' in m: meals['葷食'] += 1
            elif '素' in m: meals['素食'] += 1
            else: meals['不需餐食/其他'] += 1
            status = r.get('portrait_consent_status') or '未填'
            special_notes.append({'name': r.get('name'), 'company': r.get('company'), 'note': ('同意肖像權' if '同意' in status and '不同意' not in status else ('不同意肖像權' if '不同意' in status else '未填'))})
        return jsonify(success=True, meals=meals, special_notes=special_notes)
    finally:
        conn.close()

@app.route('/api/table_detail')
def table_detail():
    admin, sheet = q_event()
    seat = request.args.get('seat') or request.args.get('table') or ''
    conn = get_db_connection(); ensure_core_tables(conn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s AND seat=%s ORDER BY id", (admin, sheet, seat))
            people = [public_user(r) for r in cur.fetchall()]
        return jsonify(success=True, people=people, data=people)
    finally:
        conn.close()

@app.route('/api/table_group_detail')
def table_group_detail():
    return table_detail()

# -----------------------------
# Agenda / industry / exhibitors
# -----------------------------
@app.route('/api/agenda', methods=['GET','POST'])
def agenda_api():
    conn = None
    try:
        admin, sheet = q_event()
        conn = get_db_connection(); ensure_core_tables(conn); ensure_config(conn, admin, sheet)
        if request.method == 'GET':
            with conn.cursor() as cur:
                cur.execute("SELECT time, COALESCE(NULLIF(event,''), title) event, title, description FROM agenda_items WHERE admin_username=%s AND google_sheet_name=%s ORDER BY sort_order,id", (admin, sheet))
                data = cur.fetchall()
            if not data:
                data = json_loads(get_config(conn, admin, sheet).get('agenda'), [])
            return jsonify(success=True, data=data, agenda=data)
        payload = request.get_json(silent=True) or {}
        agenda = payload.get('agenda') or payload.get('data') or []
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agenda_items WHERE admin_username=%s AND google_sheet_name=%s", (admin, sheet))
            for i, a in enumerate(agenda):
                title = a.get('event') or a.get('title') or a.get('name') or ''
                cur.execute("INSERT INTO agenda_items (admin_username,google_sheet_name,sort_order,time,event,title,description) VALUES (%s,%s,%s,%s,%s,%s,%s)", (admin, sheet, i, a.get('time') or a.get('start_time') or '', title, title, a.get('description') or a.get('subtitle') or ''))
        save_config(conn, admin, sheet, {'agenda': agenda, 'dashboard_agenda_config': json.dumps(agenda, ensure_ascii=False)})
        return jsonify(success=True)
    except Exception as e:
        if conn: conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn: conn.close()

@app.route('/api/schedule', methods=['GET','POST'])
def schedule_api():
    return agenda_api()

@app.route('/api/industry_mapping', methods=['GET','POST'])
def industry_api():
    conn = None
    try:
        admin, sheet = q_event()
        conn = get_db_connection(); ensure_core_tables(conn); ensure_config(conn, admin, sheet)
        if request.method == 'GET':
            with conn.cursor() as cur:
                cur.execute("SELECT company_name, company_name company, industry FROM industry_mappings WHERE admin_username=%s AND google_sheet_name=%s ORDER BY sort_order,id", (admin, sheet))
                data = cur.fetchall()
            return jsonify(success=True, data=data)
        mappings = (request.get_json(silent=True) or {}).get('mappings') or []
        with conn.cursor() as cur:
            cur.execute("DELETE FROM industry_mappings WHERE admin_username=%s AND google_sheet_name=%s", (admin, sheet))
            for i, m in enumerate(mappings):
                company = m.get('company_name') or m.get('company') or ''
                cur.execute("INSERT INTO industry_mappings (admin_username,google_sheet_name,sort_order,company_name,industry) VALUES (%s,%s,%s,%s,%s)", (admin, sheet, i, company, m.get('industry') or ''))
        save_config(conn, admin, sheet, {'industry_mappings': mappings})
        return jsonify(success=True)
    except Exception as e:
        if conn: conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn: conn.close()

@app.route('/api/companies/list')
def companies_list():
    admin, sheet = q_event()
    conn = get_db_connection(); ensure_core_tables(conn)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT company name, COUNT(*) people_count,
                SUM(CASE WHEN status IN ('checked_in','已報到','替代','done') THEN 1 ELSE 0 END) checked_count
                FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s AND company<>'' GROUP BY company ORDER BY company
            """, (admin, sheet))
            return jsonify(success=True, companies=cur.fetchall())
    finally:
        conn.close()

@app.route('/api/exhibitors', methods=['GET','POST'])
def exhibitors_api():
    conn = None
    try:
        admin, sheet = q_event()
        conn = get_db_connection(); ensure_core_tables(conn)
        if request.method == 'GET':
            with conn.cursor() as cur:
                cur.execute("SELECT name, industry, image_url, image_url image, website, contact, description FROM exhibitors WHERE admin_username=%s AND google_sheet_name=%s ORDER BY sort_order,id", (admin, sheet))
                data = cur.fetchall()
            return jsonify(success=True, exhibitors=data)
        items = (request.get_json(silent=True) or {}).get('exhibitors') or []
        with conn.cursor() as cur:
            cur.execute("DELETE FROM exhibitors WHERE admin_username=%s AND google_sheet_name=%s", (admin, sheet))
            for i, x in enumerate(items):
                cur.execute("INSERT INTO exhibitors (admin_username,google_sheet_name,sort_order,name,industry,image_url,website,contact,description) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (admin, sheet, i, x.get('name',''), x.get('industry',''), x.get('image_url') or x.get('image') or '', x.get('website',''), x.get('contact',''), x.get('description','')))
        conn.commit()
        return jsonify(success=True)
    except Exception as e:
        if conn: conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn: conn.close()

if __name__ == '__main__':
    port = int(os.getenv('PORT', '10000'))
    app.run(host='0.0.0.0', port=port)
