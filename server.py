import os
import re
import csv
import io
import json
from datetime import datetime
from urllib.parse import urlparse, quote

from flask import Flask, request, jsonify, session, send_from_directory, Response
from flask_cors import CORS
import pymysql
from pymysql.cursors import DictCursor

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.getenv('SECRET_KEY', 'smart-ark-dev-secret')
CORS(app, supports_credentials=True)

DEFAULT_ADMIN = os.getenv('ADMIN_USERNAME', 'admin')
DEFAULT_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')
DEFAULT_SHEET = os.getenv('ADMIN_DEFAULT_EVENT') or os.getenv('ADMIN_DEFAULT_EVENTS') or '活動報到名單'
CHECKED_STATUSES = ('checked_in', '已報到', '替代', 'done')

# -----------------------------
# DB helpers
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
        SELECT COUNT(*) AS c
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s
    """, (table, col))
    return int((cur.fetchone() or {}).get('c') or 0) > 0


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
            event_subtitle VARCHAR(255),
            event_date_start VARCHAR(50),
            event_date_end VARCHAR(50),
            brand_name VARCHAR(255),
            logo_url LONGTEXT,
            banner_image_url LONGTEXT,
            map_image_url LONGTEXT,
            products LONGTEXT,
            industry_mappings LONGTEXT,
            agenda LONGTEXT,
            exhibitors LONGTEXT,
            event_config LONGTEXT,
            success_card_config LONGTEXT,
            success_info_cards_config LONGTEXT,
            dashboard_agenda_config LONGTEXT,
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
            KEY idx_status (status),
            KEY idx_phone (phone),
            KEY idx_name (name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS agenda_items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            admin_username VARCHAR(120) NOT NULL DEFAULT 'admin',
            google_sheet_name VARCHAR(255) NOT NULL DEFAULT '活動報到名單',
            sort_order INT DEFAULT 0,
            time VARCHAR(60),
            title VARCHAR(255),
            event VARCHAR(255),
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
            keyword VARCHAR(255),
            industry VARCHAR(255),
            category VARCHAR(255),
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
            company_name VARCHAR(255),
            industry VARCHAR(255),
            image_url LONGTEXT,
            logo LONGTEXT,
            website LONGTEXT,
            contact VARCHAR(255),
            description LONGTEXT,
            sort_order INT DEFAULT 0,
            KEY idx_exhibitor (admin_username, google_sheet_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        migrations = {
            'admins': {
                'allowed_events': 'LONGTEXT',
                'current_event': 'VARCHAR(255)',
            },
            'event_configs': {
                'admin_username': "VARCHAR(120) NOT NULL DEFAULT 'admin'",
                'google_sheet_name': "VARCHAR(255) NOT NULL DEFAULT '活動報到名單'",
                'event_title': 'VARCHAR(255)',
                'event_subtitle': 'VARCHAR(255)',
                'event_date_start': 'VARCHAR(50)',
                'event_date_end': 'VARCHAR(50)',
                'date_start': 'VARCHAR(50)',
                'date_end': 'VARCHAR(50)',
                'brand_name': 'VARCHAR(255)',
                'logo_url': 'LONGTEXT',
                'banner_image_url': 'LONGTEXT',
                'map_image_url': 'LONGTEXT',
                'products': 'LONGTEXT',
                'industry_mappings': 'LONGTEXT',
                'agenda': 'LONGTEXT',
                'exhibitors': 'LONGTEXT',
                'event_config': 'LONGTEXT',
                'success_card_config': 'LONGTEXT',
                'success_info_cards_config': 'LONGTEXT',
                'dashboard_agenda_config': 'LONGTEXT',
            },
            'event_registrations': {
                'admin_username': "VARCHAR(120) NOT NULL DEFAULT 'admin'",
                'google_sheet_name': "VARCHAR(255) NOT NULL DEFAULT '活動報到名單'",
                'name': 'VARCHAR(255)',
                'phone': 'VARCHAR(100)',
                'email': 'VARCHAR(255)',
                'company': 'VARCHAR(255)',
                'job_title': 'VARCHAR(255)',
                'seat': 'VARCHAR(100)',
                'status': "VARCHAR(40) DEFAULT 'pending'",
                'is_original': 'TINYINT(1) DEFAULT 1',
                'proxy_name': 'VARCHAR(255)',
                'proxy_phone': 'VARCHAR(100)',
                'checked_in_at': 'DATETIME NULL',
                'portrait_consent': 'TINYINT(1) NULL',
                'portrait_consent_status': 'VARCHAR(40)',
                'special_notes': 'LONGTEXT',
                'raw_data': 'LONGTEXT',
            },
            'agenda_items': {
                'admin_username': "VARCHAR(120) NOT NULL DEFAULT 'admin'",
                'google_sheet_name': "VARCHAR(255) NOT NULL DEFAULT '活動報到名單'",
                'sort_order': 'INT DEFAULT 0',
                'time': 'VARCHAR(60)',
                'title': 'VARCHAR(255)',
                'event': 'VARCHAR(255)',
                'description': 'LONGTEXT',
            },
            'industry_mappings': {
                'admin_username': "VARCHAR(120) NOT NULL DEFAULT 'admin'",
                'google_sheet_name': "VARCHAR(255) NOT NULL DEFAULT '活動報到名單'",
                'company_name': 'VARCHAR(255)',
                'keyword': 'VARCHAR(255)',
                'industry': 'VARCHAR(255)',
                'category': 'VARCHAR(255)',
                'sort_order': 'INT DEFAULT 0',
            },
            'exhibitors': {
                'admin_username': "VARCHAR(120) NOT NULL DEFAULT 'admin'",
                'google_sheet_name': "VARCHAR(255) NOT NULL DEFAULT '活動報到名單'",
                'name': 'VARCHAR(255)',
                'company_name': 'VARCHAR(255)',
                'industry': 'VARCHAR(255)',
                'image_url': 'LONGTEXT',
                'logo': 'LONGTEXT',
                'website': 'LONGTEXT',
                'contact': 'VARCHAR(255)',
                'description': 'LONGTEXT',
                'sort_order': 'INT DEFAULT 0',
            }
        }
        for table, cols in migrations.items():
            for col, spec in cols.items():
                add_col(cur, table, col, spec)

        cur.execute("SELECT id FROM admins WHERE username=%s", (DEFAULT_ADMIN,))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO admins (username, password, allowed_events, current_event)
                VALUES (%s,%s,%s,%s)
            """, (DEFAULT_ADMIN, DEFAULT_PASSWORD, DEFAULT_SHEET, DEFAULT_SHEET))
    conn.commit()


def payload_json():
    return request.get_json(silent=True) or {}


def event_args():
    data = payload_json() if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') else {}
    admin = request.args.get('admin') or data.get('admin') or data.get('admin_username') or session.get('username') or DEFAULT_ADMIN
    sheet = request.args.get('sheet') or request.args.get('google_sheet_name') or data.get('sheet') or data.get('google_sheet_name') or session.get('current_admin_sheet') or DEFAULT_SHEET
    return str(admin).strip() or DEFAULT_ADMIN, str(sheet).strip() or DEFAULT_SHEET


def json_loads(v, default):
    if v is None or v == '':
        return default
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default


def pick(row, keys):
    for k in keys:
        if k in row and str(row[k]).strip():
            return str(row[k]).strip()
    return ''


def clean_phone(v):
    return re.sub(r'\D+', '', str(v or ''))


def checked(status):
    return str(status or '').strip() in CHECKED_STATUSES


def normalize_registration(row):
    # 餐飲 / meal 已停用：CSV 只讀取核心名單欄位，不再記錄 meal_choice。
    return {
        'name': pick(row, ['姓名','name','Name','名字','貴賓姓名','學員姓名']),
        'phone': pick(row, ['手機','電話','phone','Phone','行動電話','手機號碼','手機號']),
        'email': pick(row, ['Email','email','E-mail','信箱','電子郵件']),
        'company': pick(row, ['公司','公司名稱','服務單位','單位','company','Company','公司/單位']),
        'job_title': pick(row, ['職稱','title','job_title','職位','position','Position']),
        'seat': pick(row, ['桌號','座位','桌次','seat','Seat','桌號/座位']),
        'special_notes': pick(row, ['備註','notes','note','特殊備註']),
        'raw_data': json.dumps(row, ensure_ascii=False)
    }


def normalize_public_user(row):
    r = dict(row or {})
    # 兼容舊前端命名
    if 'company_name' not in r:
        r['company_name'] = r.get('company') or ''
    if 'seating_chart' not in r:
        r['seating_chart'] = r.get('seat') or ''
    if 'checkin_time' not in r:
        r['checkin_time'] = r.get('checked_in_at')
    r['checkedInAt'] = ''
    if r.get('checked_in_at'):
        try:
            r['checkedInAt'] = r['checked_in_at'].strftime('%H:%M:%S')
        except Exception:
            r['checkedInAt'] = str(r.get('checked_in_at') or '')
    portrait_status = r.get('portrait_consent_status')
    if not portrait_status:
        if r.get('portrait_consent') in (1, True, '1', 'true', '同意'):
            portrait_status = '同意'
        elif r.get('portrait_consent') in (0, False, '0', 'false', '不同意'):
            portrait_status = '不同意'
        else:
            portrait_status = '未填'
    r['portrait_consent_status'] = portrait_status
    # 餐飲停用：對外不再回傳 meal 欄位，避免前後台不一致。
    for k in ('meal', 'meal_choice', 'meal_preference', 'original_meal_choice', 'food', 'diet'):
        r.pop(k, None)
    return r


def ensure_config(conn, admin, sheet):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT IGNORE INTO event_configs (admin_username, google_sheet_name, event_title, event_config, products, industry_mappings, agenda, exhibitors)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (admin, sheet, sheet, '{}', '[]', '[]', '[]', '[]'))
    conn.commit()


def config_row(conn, admin, sheet):
    ensure_config(conn, admin, sheet)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM event_configs WHERE admin_username=%s AND google_sheet_name=%s LIMIT 1", (admin, sheet))
        row = cur.fetchone() or {}
    cfg = json_loads(row.get('event_config'), {})
    for k, v in row.items():
        if k not in ('event_config',) and v not in (None, ''):
            cfg[k] = v
    cfg['google_sheet_name'] = sheet
    cfg['admin_username'] = admin
    cfg['products'] = json_loads(row.get('products'), cfg.get('products') or [])
    cfg['industry_mappings'] = json_loads(row.get('industry_mappings'), cfg.get('industry_mappings') or [])
    cfg['agenda'] = json_loads(row.get('agenda'), cfg.get('agenda') or [])
    cfg['exhibitors'] = json_loads(row.get('exhibitors'), cfg.get('exhibitors') or [])
    return cfg


def save_config(conn, admin, sheet, payload):
    ensure_config(conn, admin, sheet)
    payload = dict(payload or {})
    payload.pop('admin', None)
    payload.pop('sheet', None)
    payload.pop('meal', None)
    payload.pop('meal_choice', None)
    payload.pop('meal_preference', None)
    payload.pop('show_meal_options', None)

    # Store everything in JSON, and mirror common fields to columns for old code.
    current = config_row(conn, admin, sheet)
    current.update(payload)

    column_keys = [
        'event_title','event_subtitle','event_date_start','event_date_end','date_start','date_end',
        'brand_name','logo_url','banner_image_url','map_image_url','products','industry_mappings',
        'agenda','exhibitors','success_card_config','success_info_cards_config','dashboard_agenda_config'
    ]
    set_parts, vals = [], []
    with conn.cursor() as cur:
        for k in column_keys:
            if k in current and column_exists(cur, 'event_configs', k):
                val = current[k]
                if isinstance(val, (list, dict)):
                    val = json.dumps(val, ensure_ascii=False)
                set_parts.append(f"`{k}`=%s")
                vals.append(val)
        set_parts.append("event_config=%s")
        vals.append(json.dumps(current, ensure_ascii=False))
        vals.extend([admin, sheet])
        cur.execute(f"UPDATE event_configs SET {', '.join(set_parts)} WHERE admin_username=%s AND google_sheet_name=%s", vals)
    conn.commit()
    return config_row(conn, admin, sheet)


def get_allowed_sheets(conn, username):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM admins WHERE username=%s LIMIT 1", (username,))
        row = cur.fetchone()
    allowed = []
    current = DEFAULT_SHEET
    if row:
        allowed = [x.strip() for x in str(row.get('allowed_events') or '').split(',') if x.strip()]
        current = row.get('current_event') or (allowed[0] if allowed else DEFAULT_SHEET)
    if not allowed:
        allowed = session.get('allowed_sheets') or [DEFAULT_SHEET]
    if current not in allowed:
        current = allowed[0]
    return allowed, current


def update_current_sheet(conn, username, sheet):
    allowed, _ = get_allowed_sheets(conn, username)
    if sheet not in allowed:
        allowed.append(sheet)
    with conn.cursor() as cur:
        cur.execute("UPDATE admins SET allowed_events=%s, current_event=%s WHERE username=%s", (','.join(allowed), sheet, username))
    conn.commit()
    session['allowed_sheets'] = allowed
    session['current_admin_sheet'] = sheet
    return allowed, sheet

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

@app.route('/projection')
def projection_page():
    return send_from_directory('.', 'dashboard.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('.', path)

# -----------------------------
# Health / Auth
# -----------------------------
@app.route('/api/health')
def health():
    return jsonify(success=True, message='ok', time=datetime.now().isoformat())

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
        if conn:
            conn.close()

@app.route('/api/db_check')
def db_check():
    conn = None
    try:
        conn = get_db_connection()
        ensure_core_tables(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT DATABASE() db, NOW() now_time')
            data = cur.fetchone()
        return jsonify(success=True, data=data)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/login', methods=['POST'])
def api_login():
    conn = None
    try:
        data = payload_json()
        u = str(data.get('username') or '').strip()
        p = str(data.get('password') or '').strip()
        conn = get_db_connection()
        ensure_core_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM admins WHERE TRIM(username)=%s AND TRIM(password)=%s LIMIT 1", (u, p))
            admin = cur.fetchone()
        if not admin:
            return jsonify(success=False, message='帳密錯誤'), 401
        allowed = [x.strip() for x in str(admin.get('allowed_events') or DEFAULT_SHEET).split(',') if x.strip()] or [DEFAULT_SHEET]
        current = admin.get('current_event') or allowed[0]
        if current not in allowed:
            current = allowed[0]
        session['admin_logged_in'] = True
        session['username'] = admin['username']
        session['allowed_sheets'] = allowed
        session['current_admin_sheet'] = current
        return jsonify(success=True, username=admin['username'], allowed_sheets=allowed, current_sheet=current)
    except Exception as e:
        return jsonify(success=False, message=f'登入 API 失敗：{e}'), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/logout')
def api_logout():
    session.clear()
    return jsonify(success=True)

@app.route('/api/register', methods=['POST'])
def api_register():
    conn = None
    try:
        data = payload_json()
        username = str(data.get('username') or '').strip()
        password = str(data.get('password') or '').strip()
        allowed = str(data.get('allowed_events') or DEFAULT_SHEET).strip()
        code = str(data.get('invite_code') or '').strip()
        required = os.getenv('REGISTER_INVITE_CODE', '').strip()
        if required and code != required:
            return jsonify(success=False, message='邀請碼錯誤'), 403
        if len(username) < 3 or len(password) < 6:
            return jsonify(success=False, message='帳號至少 3 字元、密碼至少 6 碼'), 400
        conn = get_db_connection()
        ensure_core_tables(conn)
        first = allowed.split(',')[0].strip() or DEFAULT_SHEET
        with conn.cursor() as cur:
            cur.execute("INSERT INTO admins (username,password,allowed_events,current_event) VALUES (%s,%s,%s,%s)", (username, password, allowed, first))
        conn.commit()
        return jsonify(success=True, message='管理員建立成功')
    except pymysql.err.IntegrityError:
        return jsonify(success=False, message='帳號已存在'), 409
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/debug_login')
def debug_login():
    conn = None
    try:
        conn = get_db_connection()
        ensure_core_tables(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT username, allowed_events, current_event FROM admins ORDER BY id')
            rows = cur.fetchall()
        return jsonify(success=True, admins=rows, session=dict(session))
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

# -----------------------------
# Sheets / Event switching
# -----------------------------
@app.route('/api/sheets/list')
def sheets_list():
    conn = None
    try:
        username = request.args.get('admin') or session.get('username') or DEFAULT_ADMIN
        conn = get_db_connection()
        ensure_core_tables(conn)
        sheets, current = get_allowed_sheets(conn, username)
        session['allowed_sheets'] = sheets
        session['current_admin_sheet'] = current
        return jsonify(success=True, username=username, sheets=sheets, allowed_sheets=sheets, current_sheet=current)
    except Exception as e:
        return jsonify(success=False, message=f'MySQL 場次列表讀取失敗：{e}', sheets=[]), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/current_sheet')
def current_sheet():
    conn = None
    try:
        username = request.args.get('admin') or session.get('username') or DEFAULT_ADMIN
        conn = get_db_connection()
        ensure_core_tables(conn)
        sheets, current = get_allowed_sheets(conn, username)
        return jsonify(success=True, username=username, sheets=sheets, current_sheet=current)
    except Exception as e:
        fallback = session.get('current_admin_sheet') or DEFAULT_SHEET
        return jsonify(success=True, current_sheet=fallback, warning=str(e))
    finally:
        if conn:
            conn.close()

@app.route('/api/session/sheet', methods=['POST'])
def session_sheet():
    conn = None
    try:
        data = payload_json()
        username = data.get('admin') or session.get('username') or DEFAULT_ADMIN
        sheet = str(data.get('sheet') or data.get('google_sheet_name') or '').strip()
        if not sheet:
            return jsonify(success=False, message='缺少 sheet'), 400
        conn = get_db_connection()
        ensure_core_tables(conn)
        allowed, current = update_current_sheet(conn, username, sheet)
        ensure_config(conn, username, sheet)
        return jsonify(success=True, username=username, current_sheet=current, sheets=allowed)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

# -----------------------------
# Config
# -----------------------------
@app.route('/api/config', methods=['GET', 'POST', 'PUT'])
def api_config():
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        if request.method == 'GET':
            return jsonify(config_row(conn, admin, sheet))
        cfg = save_config(conn, admin, sheet, payload_json())
        return jsonify(success=True, config=cfg)
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/event-config')
def event_config_get():
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        cfg = config_row(conn, admin, sheet)
        return jsonify(success=True, config=cfg, **cfg)
    except Exception as e:
        return jsonify(success=False, message=str(e), config={}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/admin/event-config', methods=['POST', 'PUT'])
def admin_event_config_save():
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        cfg = save_config(conn, admin, sheet, payload_json())
        return jsonify(success=True, message='設定已儲存', config=cfg)
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

# -----------------------------
# Products
# -----------------------------
@app.route('/api/products', methods=['GET', 'POST', 'PUT'])
def api_products():
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        cfg = config_row(conn, admin, sheet)
        if request.method == 'GET':
            return jsonify(success=True, products=cfg.get('products') or [])
        data = payload_json()
        products = data.get('products') if isinstance(data.get('products'), list) else []
        cfg = save_config(conn, admin, sheet, {'products': products})
        return jsonify(success=True, products=cfg.get('products') or [])
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

# -----------------------------
# Agenda / Schedule
# -----------------------------
def agenda_rows(conn, admin, sheet):
    with conn.cursor() as cur:
        cur.execute("SELECT time, COALESCE(title,event) title, description, sort_order FROM agenda_items WHERE admin_username=%s AND google_sheet_name=%s ORDER BY sort_order, id", (admin, sheet))
        rows = cur.fetchall()
    if rows:
        return rows
    cfg = config_row(conn, admin, sheet)
    return cfg.get('agenda') or []


def save_agenda_rows(conn, admin, sheet, rows):
    rows = rows or []
    with conn.cursor() as cur:
        cur.execute("DELETE FROM agenda_items WHERE admin_username=%s AND google_sheet_name=%s", (admin, sheet))
        for i, r in enumerate(rows):
            cur.execute("""
                INSERT INTO agenda_items (admin_username, google_sheet_name, sort_order, time, title, event, description)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (admin, sheet, int(r.get('sort_order', i) or i), r.get('time',''), r.get('title') or r.get('event') or '', r.get('event') or r.get('title') or '', r.get('description','')))
    save_config(conn, admin, sheet, {'agenda': rows})
    conn.commit()

@app.route('/api/agenda', methods=['GET', 'POST', 'PUT'])
def api_agenda():
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        if request.method == 'GET':
            rows = agenda_rows(conn, admin, sheet)
            return jsonify(success=True, agenda=rows, schedule=rows)
        data = payload_json()
        rows = data.get('agenda') or data.get('schedule') or []
        save_agenda_rows(conn, admin, sheet, rows)
        return jsonify(success=True, agenda=agenda_rows(conn, admin, sheet))
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/schedule', methods=['GET'])
def api_schedule_get():
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        rows = agenda_rows(conn, admin, sheet)
        return jsonify(success=True, schedule=rows, agenda=rows)
    except Exception as e:
        return jsonify(success=False, message=str(e), schedule=[]), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/admin/schedule', methods=['POST', 'PUT'])
def api_schedule_save():
    return api_agenda()

# -----------------------------
# Industry mappings / Exhibitors
# -----------------------------
def get_industry_mappings(conn, admin, sheet):
    with conn.cursor() as cur:
        cur.execute("SELECT company_name, keyword, COALESCE(category, industry) category, industry, sort_order FROM industry_mappings WHERE admin_username=%s AND google_sheet_name=%s ORDER BY sort_order, id", (admin, sheet))
        rows = cur.fetchall()
    if rows:
        out = []
        for r in rows:
            out.append({
                'company_name': r.get('company_name') or r.get('keyword') or '',
                'keyword': r.get('keyword') or r.get('company_name') or '',
                'category': r.get('category') or r.get('industry') or '其他',
                'industry': r.get('industry') or r.get('category') or '其他',
                'sort_order': r.get('sort_order') or 0,
            })
        return out
    return config_row(conn, admin, sheet).get('industry_mappings') or []

@app.route('/api/industry-mappings', methods=['GET', 'POST', 'PUT'])
def api_industry_mappings():
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        if request.method == 'GET':
            return jsonify(success=True, mappings=get_industry_mappings(conn, admin, sheet), industry_mappings=get_industry_mappings(conn, admin, sheet))
        rows = payload_json().get('mappings') or payload_json().get('industry_mappings') or []
        with conn.cursor() as cur:
            cur.execute("DELETE FROM industry_mappings WHERE admin_username=%s AND google_sheet_name=%s", (admin, sheet))
            for i, r in enumerate(rows):
                keyword = r.get('keyword') or r.get('company_name') or r.get('company') or ''
                category = r.get('category') or r.get('industry') or '其他'
                cur.execute("""
                    INSERT INTO industry_mappings (admin_username, google_sheet_name, company_name, keyword, industry, category, sort_order)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (admin, sheet, r.get('company_name') or keyword, keyword, category, category, i))
        save_config(conn, admin, sheet, {'industry_mappings': rows})
        conn.commit()
        return jsonify(success=True, mappings=get_industry_mappings(conn, admin, sheet))
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/companies')
def api_companies():
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT company FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s AND COALESCE(company,'')<>'' ORDER BY company", (admin, sheet))
            rows = [r.get('company') for r in cur.fetchall()]
        return jsonify(success=True, companies=rows)
    except Exception as e:
        return jsonify(success=False, message=str(e), companies=[]), 500
    finally:
        if conn:
            conn.close()


def get_exhibitors(conn, admin, sheet):
    with conn.cursor() as cur:
        cur.execute("SELECT name, company_name, industry, image_url, logo, website, contact, description, sort_order FROM exhibitors WHERE admin_username=%s AND google_sheet_name=%s ORDER BY sort_order, id", (admin, sheet))
        rows = cur.fetchall()
    if rows:
        return [{
            'name': r.get('name') or r.get('company_name') or '',
            'company_name': r.get('company_name') or r.get('name') or '',
            'industry': r.get('industry') or '',
            'image_url': r.get('image_url') or r.get('logo') or '',
            'logo': r.get('logo') or r.get('image_url') or '',
            'website': r.get('website') or '',
            'contact': r.get('contact') or '',
            'description': r.get('description') or '',
            'sort_order': r.get('sort_order') or 0,
        } for r in rows]
    return config_row(conn, admin, sheet).get('exhibitors') or []

@app.route('/api/exhibitors', methods=['GET', 'POST', 'PUT'])
def api_exhibitors():
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        if request.method == 'GET':
            return jsonify(success=True, exhibitors=get_exhibitors(conn, admin, sheet))
        rows = payload_json().get('exhibitors') or []
        with conn.cursor() as cur:
            cur.execute("DELETE FROM exhibitors WHERE admin_username=%s AND google_sheet_name=%s", (admin, sheet))
            for i, r in enumerate(rows):
                name = r.get('name') or r.get('company_name') or r.get('company') or ''
                image_url = r.get('image_url') or r.get('logo') or ''
                cur.execute("""
                    INSERT INTO exhibitors (admin_username, google_sheet_name, name, company_name, industry, image_url, logo, website, contact, description, sort_order)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (admin, sheet, name, name, r.get('industry',''), image_url, image_url, r.get('website',''), r.get('contact',''), r.get('description',''), i))
        save_config(conn, admin, sheet, {'exhibitors': rows})
        conn.commit()
        return jsonify(success=True, exhibitors=get_exhibitors(conn, admin, sheet))
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

# -----------------------------
# Registrations / Search / Check-in
# -----------------------------
@app.route('/api/sheets/import_csv', methods=['POST'])
def import_csv():
    conn = None
    try:
        admin, sheet = event_args()
        if 'file' not in request.files:
            return jsonify(success=False, message='請選擇 CSV 檔案'), 400
        file = request.files['file']
        raw = file.read()
        text = raw.decode('utf-8-sig', errors='replace')
        reader = csv.DictReader(io.StringIO(text))
        rows = [normalize_registration(r) for r in reader]
        conn = get_db_connection()
        ensure_core_tables(conn)
        ensure_config(conn, admin, sheet)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s", (admin, sheet))
            for r in rows:
                cur.execute("""
                    INSERT INTO event_registrations
                    (admin_username, google_sheet_name, name, phone, email, company, job_title, seat, status, special_notes, raw_data)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s)
                """, (admin, sheet, r['name'], r['phone'], r['email'], r['company'], r['job_title'], r['seat'], r['special_notes'], r['raw_data']))
        conn.commit()
        return jsonify(success=True, count=len(rows), message=f'已匯入 {len(rows)} 筆名單')
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/sheets/export_csv')
def export_csv():
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT name, phone, company, email, job_title, seat, status, checked_in_at,
                       proxy_name, proxy_phone, portrait_consent_status, special_notes
                FROM event_registrations
                WHERE admin_username=%s AND google_sheet_name=%s
                ORDER BY id ASC
            """, (admin, sheet))
            rows = cur.fetchall()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['姓名','手機','公司/單位','電子郵件','職稱','桌號/座位','報到狀態','報到時間','代理/替代人','代理/替代手機','肖像權狀態','備註'])
        for r in rows:
            checked_at = r.get('checked_in_at')
            if hasattr(checked_at, 'strftime'):
                checked_at = checked_at.strftime('%Y-%m-%d %H:%M:%S')
            writer.writerow([r.get('name',''), r.get('phone',''), r.get('company',''), r.get('email',''), r.get('job_title',''), r.get('seat',''), r.get('status',''), checked_at or '', r.get('proxy_name',''), r.get('proxy_phone',''), r.get('portrait_consent_status',''), r.get('special_notes','')])
        data = '\ufeff' + out.getvalue()
        filename = f"{re.sub(r'[^\w\u4e00-\u9fff-]+','_',sheet)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(data, mimetype='text/csv; charset=utf-8', headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename)}"})
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()


def search_rows(field, value, admin, sheet):
    conn = get_db_connection()
    try:
        ensure_core_tables(conn)
        v = str(value or '').strip()
        with conn.cursor() as cur:
            if field == 'phone':
                like = f"%{clean_phone(v)}%"
                cur.execute("""
                    SELECT * FROM event_registrations
                    WHERE admin_username=%s AND google_sheet_name=%s
                      AND REPLACE(REPLACE(REPLACE(COALESCE(phone,''),'-',''),' ',''),'+','') LIKE %s
                    ORDER BY id LIMIT 50
                """, (admin, sheet, like))
            elif field == 'company':
                cur.execute("""
                    SELECT * FROM event_registrations
                    WHERE admin_username=%s AND google_sheet_name=%s AND COALESCE(company,'') LIKE %s
                    ORDER BY company, name LIMIT 100
                """, (admin, sheet, f"%{v}%"))
            else:
                cur.execute("""
                    SELECT * FROM event_registrations
                    WHERE admin_username=%s AND google_sheet_name=%s AND COALESCE(name,'') LIKE %s
                    ORDER BY name LIMIT 50
                """, (admin, sheet, f"%{v}%"))
            rows = cur.fetchall()
        return [normalize_public_user(r) for r in rows]
    finally:
        conn.close()

@app.route('/api/search/<method>')
def api_search(method):
    try:
        admin, sheet = event_args()
        val = request.args.get(method) or request.args.get('q') or request.args.get('keyword') or ''
        if method not in ('name', 'phone', 'company'):
            method = 'name'
        rows = search_rows(method, val, admin, sheet)
        return jsonify(success=True, data=rows)
    except Exception as e:
        return jsonify(success=False, message=str(e), data=[]), 500

@app.route('/api/checkin/<int:rid>', methods=['POST'])
def api_checkin(rid):
    conn = None
    try:
        data = payload_json()
        admin, sheet = event_args()
        is_original = data.get('is_original')
        if is_original is None:
            is_original = True
        proxy = data.get('proxy_info') or data.get('proxy') or {}
        portrait_bool = bool(data.get('portrait_consent', False))
        portrait_status = str(data.get('portrait_consent_status') or data.get('image_rights_status') or ('同意' if portrait_bool else '不同意')).strip()
        if portrait_status not in ('同意', '不同意', '未填'):
            portrait_status = '同意' if portrait_bool else '不同意'
        conn = get_db_connection()
        ensure_core_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM event_registrations WHERE id=%s AND admin_username=%s AND google_sheet_name=%s", (rid, admin, sheet))
            old = cur.fetchone()
            if not old:
                return jsonify(success=False, message='找不到此報到資料'), 404
            if checked(old.get('status')):
                return jsonify(success=True, already_checked=True, data=normalize_public_user(old))
            cur.execute("""
                UPDATE event_registrations
                SET status=%s, is_original=%s, proxy_name=%s, proxy_phone=%s,
                    checked_in_at=NOW(), portrait_consent=%s, portrait_consent_status=%s
                WHERE id=%s AND admin_username=%s AND google_sheet_name=%s
            """, ('checked_in' if is_original else '替代', 1 if is_original else 0, proxy.get('name',''), proxy.get('phone',''), 1 if portrait_bool else 0, portrait_status, rid, admin, sheet))
            cur.execute("SELECT * FROM event_registrations WHERE id=%s", (rid,))
            row = cur.fetchone()
        conn.commit()
        return jsonify(success=True, data=normalize_public_user(row))
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/registrations/add', methods=['POST'])
def registration_add():
    conn = None
    try:
        data = payload_json()
        admin, sheet = event_args()
        name = str(data.get('name') or '').strip()
        phone = str(data.get('phone') or '').strip()
        if not name or not phone:
            return jsonify(success=False, message='姓名與手機必填'), 400
        portrait_bool = bool(data.get('portrait_consent', False))
        portrait_status = str(data.get('portrait_consent_status') or ('同意' if portrait_bool else '不同意')).strip()
        conn = get_db_connection()
        ensure_core_tables(conn)
        ensure_config(conn, admin, sheet)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO event_registrations
                (admin_username, google_sheet_name, name, phone, email, company, job_title, seat,
                 status, checked_in_at, portrait_consent, portrait_consent_status, raw_data)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'checked_in',NOW(),%s,%s,%s)
            """, (admin, sheet, name, phone, data.get('email',''), data.get('company',''), data.get('job_title',''), data.get('seat','現場安排'), 1 if portrait_bool else 0, portrait_status, json.dumps(data, ensure_ascii=False)))
            rid = cur.lastrowid
        conn.commit()
        return jsonify(success=True, id=rid)
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

# -----------------------------
# Dashboard / Stats
# -----------------------------
def get_logs(conn, admin, sheet, limit=None, checked_only=False):
    sql = "SELECT * FROM event_registrations WHERE admin_username=%s AND google_sheet_name=%s"
    args = [admin, sheet]
    if checked_only:
        sql += " AND status IN ('checked_in','已報到','替代','done')"
    sql += " ORDER BY checked_in_at DESC, id DESC"
    if limit:
        sql += " LIMIT %s"
        args.append(int(limit))
    with conn.cursor() as cur:
        cur.execute(sql, tuple(args))
        return [normalize_public_user(r) for r in cur.fetchall()]

@app.route('/api/dashboard_stats')
def dashboard_stats():
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) total,
                       SUM(CASE WHEN status IN ('checked_in','已報到','替代','done') THEN 1 ELSE 0 END) checked
                FROM event_registrations
                WHERE admin_username=%s AND google_sheet_name=%s
            """, (admin, sheet))
            s = cur.fetchone() or {}
            total = int(s.get('total') or 0)
            checked_count = int(s.get('checked') or 0)
            cur.execute("""
                SELECT COALESCE(NULLIF(seat,''),'未分配') seat,
                       COALESCE(NULLIF(seat,''),'未分配') `table`,
                       COUNT(*) total,
                       SUM(CASE WHEN status IN ('checked_in','已報到','替代','done') THEN 1 ELSE 0 END) checked,
                       SUM(CASE WHEN status IN ('checked_in','已報到','替代','done') THEN 1 ELSE 0 END) checked_in
                FROM event_registrations
                WHERE admin_username=%s AND google_sheet_name=%s
                GROUP BY COALESCE(NULLIF(seat,''),'未分配')
                ORDER BY seat
            """, (admin, sheet))
            table_stats = cur.fetchall()
        logs = get_logs(conn, admin, sheet, 25, checked_only=True)
        industry_logs = get_logs(conn, admin, sheet, None, checked_only=True)
        return jsonify(success=True, stats={
            'total': total,
            'checked_in': checked_count,
            'checked': checked_count,
            'not_checked_in': max(total - checked_count, 0),
            'logs': logs,
            'industry_logs': industry_logs,
            'table_stats': table_stats,
        })
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/stats/meals')
def meal_stats_disabled():
    # 舊 admin 仍可能呼叫這個 API；為了不讓後台壞掉，保留入口。
    # 但餐飲功能已停用，這裡只回傳肖像權狀態，不再統計或記錄任何餐點資料。
    conn = None
    try:
        admin, sheet = event_args()
        conn = get_db_connection()
        ensure_core_tables(conn)
        logs = get_logs(conn, admin, sheet, None, checked_only=False)
        portrait_notes = []
        for r in logs:
            raw = str(r.get('portrait_consent_status') or '').strip()
            if re.search(r'不同意|拒絕|否|false|0', raw, re.I):
                note = '不同意肖像權'
            elif re.search(r'同意|yes|true|1', raw, re.I):
                note = '同意肖像權'
            else:
                note = '未填'
            portrait_notes.append({'name': r.get('name',''), 'company': r.get('company',''), 'note': note, 'portrait_consent_status': raw or '未填'})
        return jsonify(success=True, meals={}, special_notes=portrait_notes, portrait_notes=portrait_notes)
    except Exception as e:
        return jsonify(success=False, message=str(e), meals={}, special_notes=[]), 500
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    port = int(os.getenv('PORT', '10000'))
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_DEBUG') == '1')
