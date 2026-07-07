import os, json, time, requests, csv, io, re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
import pymysql
import pymysql.cursors

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get("SECRET_KEY", "rcsa_ark_secure_key_20260508_multitenant")
CORS(app)

from urllib.parse import urlparse, unquote

_CORE_TABLES_READY = False

def _clean_env(value):
    if value is None: return None
    value = str(value).strip().strip('"').strip("'")
    return value or None

def _db_config_from_url(url):
    if not url: return {}
    try:
        parsed = urlparse(url)
        if not parsed.hostname: return {}
        return {
            "host": parsed.hostname, "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "database": (parsed.path or "/railway").lstrip("/") or "railway",
            "port": parsed.port or 3306,
        }
    except Exception as e: return {}

def _get_db_config():
    url_cfg = _db_config_from_url(_clean_env(os.getenv("MYSQL_URL")) or _clean_env(os.getenv("DATABASE_URL")))
    return {
        "host": _clean_env(os.getenv("MYSQLHOST")) or _clean_env(os.getenv("DB_HOST")) or url_cfg.get("host"),
        "user": _clean_env(os.getenv("MYSQLUSER")) or _clean_env(os.getenv("DB_USER")) or url_cfg.get("user"),
        "password": _clean_env(os.getenv("MYSQLPASSWORD")) or _clean_env(os.getenv("DB_PASSWORD")) or url_cfg.get("password"),
        "database": _clean_env(os.getenv("MYSQLDATABASE")) or _clean_env(os.getenv("DB_NAME")) or url_cfg.get("database") or "railway",
        "port": int(_clean_env(os.getenv("MYSQLPORT")) or _clean_env(os.getenv("DB_PORT")) or url_cfg.get("port") or 3306),
    }

def get_db_connection():
    cfg = _get_db_config()
    return pymysql.connect(
        host=cfg["host"], user=cfg["user"], password=cfg["password"], database=cfg["database"], port=cfg["port"],
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, connect_timeout=8,
        read_timeout=20, write_timeout=20, autocommit=False
    )

def get_admin_and_event_context():
    admin_user = request.args.get('admin')
    event_key = request.args.get('sheet')
    if request.is_json:
        if not admin_user: admin_user = request.json.get('admin')
        if not event_key: event_key = request.json.get('sheet')
    if session.get('admin_logged_in'):
        admin_user = session.get('username', 'admin')
        if not event_key: event_key = session.get('current_admin_sheet')
        else: session['current_admin_sheet'] = event_key
        allowed = session.get('allowed_sheets', [])
        if event_key not in allowed and allowed:
            event_key = allowed[0]
            session['current_admin_sheet'] = event_key
    if not admin_user: admin_user = "admin"
    if not event_key: event_key = "活動報到名單"
    return admin_user, event_key

def ensure_core_tables(conn, force=False):
    global _CORE_TABLES_READY
    if _CORE_TABLES_READY and not force: return
    with conn.cursor() as cursor:
        cursor.execute("CREATE TABLE IF NOT EXISTS admins (id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(50) NOT NULL UNIQUE, password VARCHAR(100) NOT NULL, allowed_events VARCHAR(255) DEFAULT '活動報到名單', current_event VARCHAR(150) DEFAULT '活動報到名單') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        cursor.execute("CREATE TABLE IF NOT EXISTS event_configs (admin_user VARCHAR(100) NOT NULL, event_key VARCHAR(150) NOT NULL, show_meal_options BOOLEAN DEFAULT TRUE, map_image_url LONGTEXT, banner_image_url LONGTEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, PRIMARY KEY (admin_user, event_key)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        cursor.execute("CREATE TABLE IF NOT EXISTS event_products (id INT AUTO_INCREMENT PRIMARY KEY, admin_user VARCHAR(100) NOT NULL, event_key VARCHAR(150) NOT NULL, name VARCHAR(150) NOT NULL, image LONGTEXT, category VARCHAR(50) DEFAULT '課程', description TEXT, link TEXT, is_gift BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX idx_product_event (admin_user, event_key)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        cursor.execute("CREATE TABLE IF NOT EXISTS event_registrations (id INT AUTO_INCREMENT PRIMARY KEY, admin_user VARCHAR(100) NOT NULL, event_key VARCHAR(150) NOT NULL, name VARCHAR(150) NOT NULL, phone VARCHAR(50), email VARCHAR(150), company_name VARCHAR(255), region VARCHAR(100), training_level VARCHAR(100), contract_period VARCHAR(100), participant_count INT DEFAULT 1, job_title VARCHAR(150), contact_person VARCHAR(150), contact_email VARCHAR(150), seating_chart VARCHAR(100), meal_choice VARCHAR(50), original_meal_choice VARCHAR(50), status VARCHAR(50) DEFAULT '未報到', checkin_time DATETIME NULL, proxy_name VARCHAR(150), proxy_phone VARCHAR(50), portrait_consent TINYINT(1) DEFAULT NULL, portrait_consent_status VARCHAR(20), portrait_consent_time DATETIME NULL, note TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX idx_registration_event (admin_user, event_key)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        try:
            cursor.execute("INSERT INTO admins (username, password, allowed_events, current_event) VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE password = VALUES(password)", (os.getenv('ADMIN_USERNAME', 'admin'), os.getenv('ADMIN_PASSWORD', 'admin123'), os.getenv('ADMIN_DEFAULT_EVENTS', '活動報到名單'), os.getenv('ADMIN_DEFAULT_EVENT', '活動報到名單')))
        except Exception: pass
    conn.commit()
    _CORE_TABLES_READY = True

def _ignore_duplicate_column_error(exc):
    msg = str(exc).lower()
    return 'duplicate column' in msg or '1060' in msg

def ensure_dashboard_tables(conn):
    ensure_core_tables(conn)
    with conn.cursor() as cursor:
        cursor.execute("CREATE TABLE IF NOT EXISTS event_agenda (id INT AUTO_INCREMENT PRIMARY KEY, admin_user VARCHAR(100) NOT NULL, event_key VARCHAR(150) NOT NULL, time VARCHAR(50), event TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX idx_agenda_event (admin_user, event_key)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        cursor.execute("CREATE TABLE IF NOT EXISTS company_industry_mapping (id INT AUTO_INCREMENT PRIMARY KEY, admin_user VARCHAR(100) NOT NULL, event_key VARCHAR(150) NOT NULL, company_name VARCHAR(255) NOT NULL, industry VARCHAR(100), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX idx_industry_event (admin_user, event_key)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        cursor.execute("CREATE TABLE IF NOT EXISTS event_exhibitors (id INT AUTO_INCREMENT PRIMARY KEY, admin_user VARCHAR(100) NOT NULL, event_key VARCHAR(150) NOT NULL, company_name VARCHAR(255) NOT NULL, industry VARCHAR(100), logo TEXT, image_url LONGTEXT, description TEXT, website TEXT, contact TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX idx_exhibitor_event (admin_user, event_key)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
    conn.commit()

def _clean_str(value):
    return str(value) if value is not None else ''

def _to_json_safe_rows(rows):
    safe = []
    for row in rows:
        item = {}
        for key, value in row.items():
            if isinstance(value, datetime): item[key] = value.strftime('%Y-%m-%d %H:%M:%S')
            elif isinstance(value, timedelta): item[key] = str(value)
            else: item[key] = value
        safe.append(item)
    return safe

EXPERIENCE_CONFIG_DEFAULTS = {
    "event_title": "2026 全球面對面", "event_subtitle": "世代共榮的數位聚合",
    "event_date_start": "2026/06/01", "event_date_end": "2026/08/XX",
    "brand_name": "智匯方舟", "logo_url": "",
    "success_title": "智匯方舟", "success_subtitle": "世代共榮的數位聚合",
    "success_desc": "您好，已為您準備以下專屬航程資訊",
    "success_btn_text": "回到首頁", "success_btn_url": "",
    "flow_icon": "🕘", "flow_title": "大會時空座標", "flow_subtitle": "實體進化航線預載",
    "flow_description": "09:30 - 17:00 航程時間軸", "flow_url": "", "flow_enabled": True,
    "gift_icon": "🎁", "gift_title": "活動商品專區", "gift_subtitle": "精選伴手禮與補給品",
    "gift_description": "點擊進入物資艙查看", "gift_image_url": "", "gift_url": "", "gift_enabled": True,
    "video_icon": "▶", "video_title": "核心引擎啟動", "video_subtitle": "智能全新數位中控系統",
    "video_description": "三年經營現況影片", "video_url": "", "video_embed_enabled": True, "video_enabled": True,
    "map_icon": "🧭", "map_title": "2026 產業星圖", "map_subtitle": "領航員名冊與每攤機會",
    "map_description": "A-MALL.png / Navigator Directory", "map_image_url": "", "map_url": "", "map_enabled": True,
    "projection_title": "世代共榮的數位聚合", "projection_subtitle": "DIGITAL CONVERGENCE FOR GENERATIONAL PROSPERITY"
}

def ensure_experience_tables(conn):
    ensure_dashboard_tables(conn)
    config_columns = {
        'event_title': 'VARCHAR(255)', 'event_subtitle': 'VARCHAR(255)',
        'event_date_start': 'VARCHAR(50)', 'event_date_end': 'VARCHAR(50)',
        'brand_name': 'VARCHAR(255)', 'logo_url': 'LONGTEXT',
        'success_title': 'VARCHAR(255)', 'success_subtitle': 'VARCHAR(255)',
        'success_desc': 'TEXT', 'success_btn_text': 'VARCHAR(100)', 'success_btn_url': 'LONGTEXT',
        'flow_icon': 'VARCHAR(50)', 'flow_title': 'VARCHAR(255)', 'flow_subtitle': 'VARCHAR(255)',
        'flow_image_url': 'LONGTEXT', 'flow_description': 'LONGTEXT', 'flow_url': 'LONGTEXT', 'flow_enabled': 'BOOLEAN DEFAULT TRUE',
        'gift_icon': 'VARCHAR(50)', 'gift_title': 'VARCHAR(255)', 'gift_subtitle': 'VARCHAR(255)',
        'gift_image_url': 'LONGTEXT', 'gift_description': 'LONGTEXT', 'gift_url': 'LONGTEXT', 'gift_enabled': 'BOOLEAN DEFAULT TRUE',
        'video_icon': 'VARCHAR(50)', 'video_title': 'VARCHAR(255)', 'video_subtitle': 'VARCHAR(255)',
        'video_description': 'LONGTEXT', 'video_url': 'LONGTEXT', 'video_embed_enabled': 'BOOLEAN DEFAULT TRUE', 'video_enabled': 'BOOLEAN DEFAULT TRUE',
        'map_icon': 'VARCHAR(50)', 'map_title': 'VARCHAR(255)', 'map_subtitle': 'VARCHAR(255)',
        'map_image_url': 'LONGTEXT', 'map_description': 'LONGTEXT', 'map_url': 'LONGTEXT', 'map_enabled': 'BOOLEAN DEFAULT TRUE',
        'projection_title': 'VARCHAR(255)', 'projection_subtitle': 'VARCHAR(255)'
    }
    with conn.cursor() as cursor:
        for col, definition in config_columns.items():
            try: cursor.execute(f"ALTER TABLE event_configs ADD COLUMN {col} {definition}")
            except Exception as e: pass
        try: cursor.execute("ALTER TABLE event_agenda ADD COLUMN title VARCHAR(255)")
        except Exception: pass
        try: cursor.execute("ALTER TABLE event_agenda ADD COLUMN description TEXT")
        except Exception: pass
        try: cursor.execute("ALTER TABLE event_agenda ADD COLUMN sort_order INT DEFAULT 0")
        except Exception: pass
    conn.commit()

def _as_bool(value, default=False):
    if value is None: return default
    if isinstance(value, bool): return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "啟用")

def _load_event_config(conn, admin_user, event_key):
    ensure_experience_tables(conn)
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM event_configs WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
        row = cursor.fetchone()
    data = dict(EXPERIENCE_CONFIG_DEFAULTS)
    if row:
        for k in data.keys():
            if k in row and row.get(k) is not None:
                data[k] = row.get(k)
        data['map_image_url'] = row.get('map_image_url') or ''
        data['banner_image_url'] = row.get('banner_image_url') or ''
    for key in ['gift_enabled', 'video_embed_enabled', 'video_enabled', 'flow_enabled', 'map_enabled']:
        data[key] = _as_bool(data.get(key), True)
    data['admin_user'] = admin_user
    data['event_key'] = event_key
    data['google_sheet_name'] = event_key
    return data

@app.route('/api/agenda', methods=['GET', 'POST'])
def handle_agenda():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_dashboard_tables(conn)
        if request.method == 'POST':
            data = request.json or {}
            agenda_items = data.get('agenda', [])
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM event_agenda WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                for item in agenda_items:
                    time_text = _clean_str(item.get('time')).strip()
                    event_text = _clean_str(item.get('event')).strip()
                    cursor.execute("INSERT INTO event_agenda (admin_user, event_key, time, event) VALUES (%s, %s, %s, %s)", (admin_user, event_key, time_text, event_text))
            conn.commit()
            return jsonify({"success": True, "message": "議程已儲存"})
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, time, event FROM event_agenda WHERE admin_user = %s AND event_key = %s ORDER BY id ASC", (admin_user, event_key))
            items = _to_json_safe_rows(cursor.fetchall())
        return jsonify({"success": True, "data": items})
    except Exception as e: return jsonify({"success": False, "message": str(e), "data": []}), 500
    finally: conn.close()

@app.route('/api/exhibitors', methods=['GET', 'POST'])
def handle_exhibitors():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_dashboard_tables(conn)
        if request.method == 'POST':
            data = request.json or {}
            exhibitors = data.get('exhibitors', [])
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM event_exhibitors WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                for ex in exhibitors:
                    company_name = _clean_str(ex.get('name') or ex.get('company_name')).strip()
                    if not company_name: continue
                    cursor.execute("INSERT INTO event_exhibitors (admin_user, event_key, company_name, industry, logo, image_url, description, website, contact) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                                   (admin_user, event_key, company_name, _clean_str(ex.get('industry')).strip(), '', _clean_str(ex.get('image_url') or ex.get('image')).strip(), _clean_str(ex.get('description')).strip(), _clean_str(ex.get('website')).strip(), _clean_str(ex.get('contact')).strip()))
            conn.commit()
            return jsonify({"success": True, "message": "企業資訊已儲存到資料庫"})
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, company_name, industry, logo, image_url, description, website, contact FROM event_exhibitors WHERE admin_user = %s AND event_key = %s ORDER BY id ASC", (admin_user, event_key))
            exhibitors = [{"id": ex["id"], "name": ex["company_name"], "industry": ex["industry"] or "未分類", "image_url": ex["image_url"] or ex["logo"], "description": ex["description"] or ""} for ex in cursor.fetchall()]
        return jsonify({"success": True, "exhibitors": exhibitors})
    finally: conn.close()

@app.route('/api/industry_mapping', methods=['GET', 'POST'])
def handle_industry_mapping():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        if request.method == 'POST':
            mappings = (request.json or {}).get('mappings', [])
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM company_industry_mapping WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                for mapping in mappings:
                    company_name = _clean_str(mapping.get('company')).strip()
                    if company_name: cursor.execute("INSERT INTO company_industry_mapping (admin_user, event_key, company_name, industry) VALUES (%s, %s, %s, %s)", (admin_user, event_key, company_name, _clean_str(mapping.get('industry')).strip() or '未分類'))
            conn.commit()
            return jsonify({"success": True})
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, company_name, industry FROM company_industry_mapping WHERE admin_user = %s AND event_key = %s ORDER BY id ASC", (admin_user, event_key))
            return jsonify({"success": True, "data": _to_json_safe_rows(cursor.fetchall())})
    finally: conn.close()

@app.route('/api/companies/list')
def api_companies_list():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_core_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("SELECT TRIM(company_name) AS company_name, COUNT(*) AS people_count, SUM(CASE WHEN status IN ('checked_in', '已報到', '替代') THEN 1 ELSE 0 END) AS checked_count FROM event_registrations WHERE admin_user = %s AND event_key = %s AND TRIM(COALESCE(company_name, '')) <> '' GROUP BY TRIM(company_name) ORDER BY company_name ASC", (admin_user, event_key))
            companies = [{"name": r["company_name"], "people_count": r["people_count"], "checked_count": r["checked_count"]} for r in cursor.fetchall()]
        return jsonify({"success": True, "companies": companies})
    finally: conn.close()

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_experience_tables(conn)
        if request.method == 'POST':
            payload = request.json
            with conn.cursor() as cursor:
                cursor.execute("REPLACE INTO event_configs (admin_user, event_key, show_meal_options, map_image_url, banner_image_url) VALUES (%s, %s, %s, %s, %s)", (admin_user, event_key, 1, payload.get("map_image_url", ""), payload.get("banner_image_url", "")))
                if "products" in payload:
                    cursor.execute("DELETE FROM event_products WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                    for p in payload["products"]:
                        cursor.execute("INSERT INTO event_products (admin_user, event_key, name, image, category, description, link, is_gift) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (admin_user, event_key, p.get("name", ""), p.get("image", ""), p.get("category", "課程"), p.get("description", ""), p.get("link", ""), 1 if p.get("isGift") else 0))
            conn.commit()
            return jsonify({"success": True, "message": "設定儲存成功"})
        
        config_data = _load_event_config(conn, admin_user, event_key)
        config_data["products"] = []
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM event_products WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
            for r in cursor.fetchall():
                config_data["products"].append({"name": r["name"], "image": r["image"], "category": r["category"], "description": r["description"], "link": r["link"], "isGift": bool(r["is_gift"])})
            cursor.execute("SELECT company_name, industry FROM company_industry_mapping WHERE admin_user = %s AND event_key = %s ORDER BY id ASC", (admin_user, event_key))
            config_data["industry_mappings"] = [{"keyword": (m.get("company_name") or "").strip(), "category": (m.get("industry") or "未分類").strip()} for m in cursor.fetchall() if (m.get("company_name") or "").strip()]
        return jsonify(config_data)
    finally: conn.close()

@app.route('/api/search/<method>')
def search(method):
    admin_user, event_key = get_admin_and_event_context()
    q = request.args.get(method, "").strip()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if method == 'company':
                if len(q) < 2: return jsonify({"success": True, "data": []})
                cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s AND company_name LIKE %s LIMIT 50", (admin_user, event_key, f"%{q}%"))
            elif method == 'phone': cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s AND (phone LIKE %s OR name LIKE %s)", (admin_user, event_key, f"%{q}%", f"%{q}%"))
            else: cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s AND name LIKE %s", (admin_user, event_key, f"%{q}%"))
            res = cursor.fetchall()
            for r in res:
                r['id'] = str(r['id']); r['company'] = r['company_name']; r['meal'] = r['meal_choice']
                r['status_display'] = '已報到' if r['status'] in ['checked_in', '已報到', '替代'] else '未報到'
            return jsonify({"success": True, "data": res})
    finally: conn.close()

@app.route('/api/checkin/<pid>', methods=['POST'])
def checkin(pid):
    admin_user, event_key = get_admin_and_event_context()
    data = request.json or {}
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM event_registrations WHERE id = %s AND admin_user = %s AND event_key = %s", (pid, admin_user, event_key))
            user = cursor.fetchone()
            if not user: return jsonify({"success": False, "error": "user_not_found"}), 404
            if user['status'] in ['checked_in', '已報到', '替代']:
                return jsonify({"success": False, "error": "already_done", "data": {"name": user['name'], "company": user['company_name'], "seat": user['seating_chart'], "meal": user['meal_choice'], "portrait_consent_status": user.get('portrait_consent_status') or ""}}), 200
            
            is_original = data.get('is_original', True)
            # FIX: Always respect the meal chosen in the frontend if provided
            meal_choice = data.get('meal', user['meal_choice'])
            status_val = 'checked_in' if is_original else '替代'
            original_meal = user.get('original_meal_choice', user['meal_choice'])
            
            proxy_info = data.get('proxy_info') or {}
            portrait_consent = bool(data.get('portrait_consent', False))
            portrait_consent_status = _clean_str(data.get('portrait_consent_status')).strip()
            if portrait_consent_status not in ['同意', '不同意']: portrait_consent_status = '同意' if portrait_consent else '不同意'

            now = datetime.now()
            cursor.execute("""
                UPDATE event_registrations SET checkin_time = %s, status = %s, meal_choice = %s, original_meal_choice = %s, proxy_name = %s, proxy_phone = %s, portrait_consent = %s, portrait_consent_status = %s, portrait_consent_time = %s WHERE id = %s AND admin_user = %s AND event_key = %s
            """, (now, status_val, meal_choice, original_meal, proxy_info.get('name'), proxy_info.get('phone'), 1 if portrait_consent else 0, portrait_consent_status, now, pid, admin_user, event_key))
            conn.commit()
            return jsonify({"success": True, "data": {"name": user['name'], "company": user['company_name'], "job_title": user.get('job_title') or '職稱', "seat": user['seating_chart'], "meal": meal_choice}})
    except Exception as e: conn.rollback(); return jsonify({"success": False, "error": str(e)}), 500
    finally: conn.close()

@app.route('/api/dashboard_stats')
def get_dashboard_stats():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_core_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN status IN ('checked_in', '已報到', '替代') THEN 1 ELSE 0 END) AS checked_in FROM event_registrations WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
            summary = cursor.fetchone() or {}
            cursor.execute("SELECT seating_chart AS table_name, COUNT(*) AS total, SUM(CASE WHEN status IN ('checked_in', '已報到', '替代') THEN 1 ELSE 0 END) AS checked FROM event_registrations WHERE admin_user = %s AND event_key = %s AND seating_chart IS NOT NULL AND TRIM(seating_chart) NOT IN ('', '0', '第0桌', '0桌') GROUP BY seating_chart", (admin_user, event_key))
            table_rows = cursor.fetchall()
            cursor.execute("SELECT r.id, r.name, r.status, r.checkin_time, r.company_name, r.meal_choice, COALESCE(NULLIF(TRIM(m.industry), ''), NULLIF(TRIM(e.industry), ''), '其他') AS industry FROM event_registrations r LEFT JOIN company_industry_mapping m ON m.admin_user = r.admin_user AND m.event_key = r.event_key AND TRIM(m.company_name) = TRIM(r.company_name) LEFT JOIN event_exhibitors e ON e.admin_user = r.admin_user AND e.event_key = r.event_key AND TRIM(e.company_name) = TRIM(r.company_name) WHERE r.admin_user = %s AND r.event_key = %s AND r.status IN ('checked_in', '已報到', '替代') ORDER BY r.checkin_time DESC LIMIT 200", (admin_user, event_key))
            checked_logs = cursor.fetchall()
        
        table_stats_formatted = [{"table": r.get("table_name") or "", "checked": int(r.get("checked") or 0), "total": int(r.get("total") or 0)} for r in table_rows]
        logs = [{"id": r.get('id'), "name": r.get('name') or "", "time": r.get('checkin_time').strftime('%H:%M:%S') if r.get('checkin_time') else "", "company": r.get('company_name') or "", "industry": r.get('industry') or "其他", "meal": r.get('meal_choice') or ""} for r in checked_logs]
        return jsonify({"success": True, "stats": {"total": int(summary.get("total") or 0), "checked_in": int(summary.get("checked_in") or 0), "not_checked_in": int(summary.get("total") or 0) - int(summary.get("checked_in") or 0), "table_stats": table_stats_formatted, "logs": logs}})
    finally: conn.close()

def _table_group_payload(table_text):
    text = str(table_text or '').strip()
    normalized = text.replace('第', '').replace('桌', '').replace(' ', '')
    m = re.match(r'^(\d{1,3})[-_－—](\d{1,3})$', normalized)
    if m: return str(int(m.group(1))).zfill(2), normalized
    m2 = re.match(r'^(\d{1,3})$', normalized)
    if m2: return str(int(m2.group(1))).zfill(2), normalized
    return normalized, normalized

def _chinese_table_name(group_key):
    try: n = int(str(group_key))
    except Exception: return f"第 {group_key} 桌"
    zh = ['零','一','二','三','四','五','六','七','八','九','十']
    if n <= 0: return f"第 {group_key} 桌"
    if n <= 10: return f"第{zh[n]}桌"
    if n < 20: return f"第十{zh[n-10]}桌"
    tens, ones = divmod(n, 10)
    if tens < len(zh): return f"第{zh[tens]}十{zh[ones] if ones else ''}桌"
    return f"第 {n} 桌"

@app.route('/api/table_group_detail')
def api_table_group_detail():
    admin_user, event_key = get_admin_and_event_context()
    group = (request.args.get('group') or '').strip()
    normalized_group = str(int(group)).zfill(2) if str(group).isdigit() else group
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, name, phone, company_name, seating_chart, status, checkin_time, meal_choice, job_title FROM event_registrations WHERE admin_user = %s AND event_key = %s ORDER BY seating_chart ASC", (admin_user, event_key))
            all_rows = cursor.fetchall()
        rows = []
        seats = []
        for row in all_rows:
            key, seat_text = _table_group_payload(row.get("seating_chart"))
            if key == normalized_group:
                rows.append(row); seats.append(seat_text)
        
        def is_checked(r): return r.get('status') in ['checked_in', '已報到', '替代']
        def payload(r): return {"id": r.get("id"), "name": r.get("name") or "", "phone": r.get("phone") or "", "company": r.get("company_name") or "", "seat": r.get("seating_chart") or "", "meal": r.get("meal_choice") or "", "status": r.get("status") or "", "job_title": r.get("job_title") or "", "checked": is_checked(r), "checkin_time": r.get("checkin_time").strftime('%H:%M:%S') if r.get("checkin_time") else ""}
        
        return jsonify({"success": True, "group": normalized_group, "display_name": _chinese_table_name(normalized_group), "total": len(rows), "checked": len([r for r in rows if is_checked(r)]), "checked_people": [payload(r) for r in rows if is_checked(r)], "pending_people": [payload(r) for r in rows if not is_checked(r)]})
    finally: conn.close()

@app.route('/api/registrations/add', methods=['POST'])
def add_registration():
    admin_user, event_key = get_admin_and_event_context()
    data = request.json or {}
    conn = get_db_connection()
    try:
        meal = data.get('meal', '未選擇')
        portrait_consent = bool(data.get('portrait_consent', False))
        portrait_consent_status = _clean_str(data.get('portrait_consent_status')).strip()
        if portrait_consent_status not in ['同意', '不同意']: portrait_consent_status = '同意' if portrait_consent else '不同意'
        now = datetime.now()
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO event_registrations (admin_user, event_key, name, phone, company_name, seating_chart, status, checkin_time, meal_choice, original_meal_choice, portrait_consent, portrait_consent_status, portrait_consent_time, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (admin_user, event_key, data.get('name'), data.get('phone'), data.get('company'), data.get('seat', '現場安排'), '已報到', now, meal, meal, 1 if portrait_consent else 0, portrait_consent_status, now, '現場臨時報到'))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e: conn.rollback(); return jsonify({"success": False, "message": str(e)}), 500
    finally: conn.close()

@app.route('/api/event-config', methods=['GET'])
def api_event_config():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try: return jsonify({"success": True, "config": _load_event_config(conn, admin_user, event_key)})
    except Exception as e: return jsonify({"success": False, "message": str(e)}), 500
    finally: conn.close()

@app.route('/api/admin/event-config', methods=['PUT', 'POST'])
def api_admin_event_config():
    admin_user, event_key = get_admin_and_event_context()
    payload = request.get_json(silent=True) or {}
    conn = get_db_connection()
    try:
        ensure_experience_tables(conn)
        current = _load_event_config(conn, admin_user, event_key)
        data = dict(current)
        for key in EXPERIENCE_CONFIG_DEFAULTS.keys():
            if key in payload: data[key] = payload.get(key)
        for key in ['gift_enabled', 'video_embed_enabled', 'video_enabled', 'flow_enabled', 'map_enabled']:
            data[key] = 1 if _as_bool(data.get(key), True) else 0

        cols = ['admin_user', 'event_key', 'show_meal_options'] + list(EXPERIENCE_CONFIG_DEFAULTS.keys())
        values = {'admin_user': admin_user, 'event_key': event_key, 'show_meal_options': 1, **data}
        placeholders = ', '.join(['%s'] * len(cols))
        update_clause = ', '.join([f"{c}=VALUES({c})" for c in cols if c not in ('admin_user', 'event_key')])
        sql = f"INSERT INTO event_configs ({', '.join(cols)}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_clause}"
        with conn.cursor() as cursor:
            cursor.execute(sql, [values.get(c) for c in cols])
        conn.commit()
        return jsonify({"success": True, "message": "智匯方舟設定已儲存", "config": _load_event_config(conn, admin_user, event_key)})
    except Exception as e: conn.rollback(); return jsonify({"success": False, "message": str(e)}), 500
    finally: conn.close()

@app.route('/api/schedule', methods=['GET'])
def api_schedule():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, time, title, description, event, sort_order FROM event_agenda WHERE admin_user = %s AND event_key = %s ORDER BY sort_order ASC, id ASC", (admin_user, event_key))
            rows = _to_json_safe_rows(cursor.fetchall())
        schedule = [{"id": r.get('id'), "time": r.get('time') or '', "title": r.get('title') or r.get('event') or '', "description": r.get('description') or '', "sort_order": r.get('sort_order') or i} for i, r in enumerate(rows)]
        return jsonify({"success": True, "schedule": schedule})
    except Exception as e: return jsonify({"success": False, "message": str(e)}), 500
    finally: conn.close()

@app.route('/api/admin/schedule', methods=['PUT', 'POST'])
def api_admin_schedule():
    admin_user, event_key = get_admin_and_event_context()
    payload = request.get_json(silent=True) or {}
    schedule = payload.get('schedule') or payload.get('agenda') or []
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM event_agenda WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
            for idx, item in enumerate(schedule):
                time_text, title, desc = _clean_str(item.get('time')).strip(), _clean_str(item.get('title') or item.get('event')).strip(), _clean_str(item.get('description')).strip()
                if time_text or title or desc:
                    cursor.execute("INSERT INTO event_agenda (admin_user, event_key, time, event, title, description, sort_order) VALUES (%s, %s, %s, %s, %s, %s, %s)", (admin_user, event_key, time_text, f"{title}：{desc}" if desc else title, title, desc, int(item.get('sort_order') or idx)))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e: conn.rollback(); return jsonify({"success": False, "message": str(e)}), 500
    finally: conn.close()

@app.route('/api/stats/meals')
def api_meal_stats():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT meal_choice, COUNT(*) as count FROM event_registrations WHERE admin_user = %s AND event_key = %s GROUP BY meal_choice", (admin_user, event_key))
            meal_rows = cursor.fetchall()
            
            # 將備註改為撈取肖像權狀態，而非特殊飲食備註
            cursor.execute("SELECT name, company_name, portrait_consent_status as note FROM event_registrations WHERE admin_user = %s AND event_key = %s AND portrait_consent_status IS NOT NULL AND portrait_consent_status != ''", (admin_user, event_key))
            note_rows = cursor.fetchall()
            
        meals = {"葷": 0, "素": 0, "其他": 0}
        for row in meal_rows:
            choice = str(row['meal_choice'] or "未選擇").strip()
            if any(k in choice for k in ["素", "蔬", "Vegetarian", "Vegi"]): meals["素"] += row['count']
            elif any(k in choice for k in ["葷", "肉", "Meat", "Non-Veg"]): meals["葷"] += row['count']
            else: meals["其他"] += row['count']
                
        return jsonify({"success": True, "meals": meals, "special_notes": note_rows})
    finally: conn.close()

@app.route('/api/current_sheet')
def api_current_sheet():
    admin_user, event_key = get_admin_and_event_context()
    return jsonify({"success": True, "admin": admin_user, "sheet": event_key, "current_sheet": event_key})

@app.route('/api/sheets/list')
def api_sheets_list():
    username = session.get('username', 'admin')
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT allowed_events, current_event FROM admins WHERE username = %s", (username,))
            row = cursor.fetchone()
        sheets = [s.strip() for s in (row or {}).get('allowed_events', '').split(',') if s.strip()] or ['活動報到名單']
        return jsonify({"success": True, "sheets": sheets, "current_sheet": session.get('current_admin_sheet')})
    finally: conn.close()

@app.route('/api/user/info')
def api_user_info():
    if not session.get('admin_logged_in'): return jsonify({"success": False, "logged_in": False, "message": "尚未登入"}), 401
    return jsonify({"success": True, "logged_in": True, "username": session.get('username', 'admin')})

@app.route('/api/session/sheet', methods=['POST'])
def api_session_sheet():
    payload = request.get_json(silent=True) or {}
    sheet = (payload.get('sheet') or '').strip()
    session['current_admin_sheet'] = sheet
    return jsonify({"success": True, "current_sheet": sheet})

@app.route('/dashboard')
def dashboard_page(): return send_from_directory('.', 'dashboard.html')
@app.route('/admin')
def admin_page(): return send_from_directory('.', 'admin.html')
@app.route('/')
def index(): return send_from_directory('.', '活動報到系統.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
