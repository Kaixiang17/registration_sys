import os, json, time, requests, csv, io, re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
import pymysql
import pymysql.cursors

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get("SECRET_KEY", "rcsa_ark_secure_key_20260508_multitenant") 
CORS(app)

DB_HOST = os.environ.get('DB_HOST')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_NAME = os.environ.get('DB_NAME', 'defaultdb')
DB_PORT = int(os.environ.get('DB_PORT', 27632))

def get_db_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, port=DB_PORT,
        ssl={"ssl": {}}, cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=8, read_timeout=20, write_timeout=20, autocommit=False
    )

def get_admin_and_event_context():
    admin_user = request.args.get('admin')
    event_key = request.args.get('sheet')
    
    if request.is_json:
        if not admin_user: admin_user = request.json.get('admin')
        if not event_key: event_key = request.json.get('sheet')

    if session.get('admin_logged_in'):
        admin_user = session.get('username', 'admin')
        if not event_key:
            event_key = session.get('current_admin_sheet')
        else:
            session['current_admin_sheet'] = event_key
            
        allowed = session.get('allowed_sheets', [])
        if event_key not in allowed and allowed:
            event_key = allowed[0]
            session['current_admin_sheet'] = event_key

    if not admin_user: admin_user = "admin"
    if not event_key: event_key = "活動報到名單"
    
    return admin_user, event_key



def ensure_core_tables(conn):
    """
    保護 Aiven MySQL 核心資料表。
    admin.html 一進後台會先呼叫 /api/config；如果 event_configs / event_products / event_registrations
    不存在或缺少 admin_user、event_key，就會顯示「設定載入失敗」。
    """
    with conn.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(50) NOT NULL UNIQUE,
                password VARCHAR(100) NOT NULL,
                allowed_events VARCHAR(255) DEFAULT '活動報到名單',
                current_event VARCHAR(150) DEFAULT '活動報到名單'
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_configs (
                admin_user VARCHAR(100) NOT NULL,
                event_key VARCHAR(150) NOT NULL,
                show_meal_options BOOLEAN DEFAULT TRUE,
                map_image_url LONGTEXT,
                banner_image_url LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (admin_user, event_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_products (
                id INT AUTO_INCREMENT PRIMARY KEY,
                admin_user VARCHAR(100) NOT NULL,
                event_key VARCHAR(150) NOT NULL,
                name VARCHAR(150) NOT NULL,
                image LONGTEXT,
                category VARCHAR(50) DEFAULT '課程',
                description TEXT,
                link TEXT,
                is_gift BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_product_event (admin_user, event_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_registrations (
                id INT AUTO_INCREMENT PRIMARY KEY,
                admin_user VARCHAR(100) NOT NULL,
                event_key VARCHAR(150) NOT NULL,
                name VARCHAR(150) NOT NULL,
                phone VARCHAR(50),
                email VARCHAR(150),
                company_name VARCHAR(255),
                region VARCHAR(100),
                training_level VARCHAR(100),
                contract_period VARCHAR(100),
                participant_count INT DEFAULT 1,
                job_title VARCHAR(150),
                contact_person VARCHAR(150),
                contact_email VARCHAR(150),
                seating_chart VARCHAR(100),
                meal_choice VARCHAR(50),
                original_meal_choice VARCHAR(50),
                status VARCHAR(50) DEFAULT '未報到',
                checkin_time DATETIME NULL,
                proxy_name VARCHAR(150),
                proxy_phone VARCHAR(50),
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_registration_event (admin_user, event_key),
                INDEX idx_registration_name (name),
                INDEX idx_registration_phone (phone),
                INDEX idx_registration_company (company_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # 舊資料表補欄位：Aiven 之前可能已經有舊版 schema，所以不能只靠 CREATE TABLE IF NOT EXISTS。
        alter_map = {
            'admins': {
                'allowed_events': 'VARCHAR(255) DEFAULT "活動報到名單"',
                'current_event': 'VARCHAR(150) DEFAULT "活動報到名單"'
            },
            'event_configs': {
                'admin_user': 'VARCHAR(100) NOT NULL DEFAULT "admin"',
                'event_key': 'VARCHAR(150) NOT NULL DEFAULT "活動報到名單"',
                'show_meal_options': 'BOOLEAN DEFAULT TRUE',
                'map_image_url': 'LONGTEXT',
                'banner_image_url': 'LONGTEXT'
            },
            'event_products': {
                'admin_user': 'VARCHAR(100) NOT NULL DEFAULT "admin"',
                'event_key': 'VARCHAR(150) NOT NULL DEFAULT "活動報到名單"',
                'image': 'LONGTEXT',
                'category': 'VARCHAR(50) DEFAULT "課程"',
                'description': 'TEXT',
                'link': 'TEXT',
                'is_gift': 'BOOLEAN DEFAULT FALSE'
            },
            'event_registrations': {
                'admin_user': 'VARCHAR(100) NOT NULL DEFAULT "admin"',
                'event_key': 'VARCHAR(150) NOT NULL DEFAULT "活動報到名單"',
                'phone': 'VARCHAR(50)',
                'email': 'VARCHAR(150)',
                'company_name': 'VARCHAR(255)',
                'region': 'VARCHAR(100)',
                'training_level': 'VARCHAR(100)',
                'contract_period': 'VARCHAR(100)',
                'participant_count': 'INT DEFAULT 1',
                'job_title': 'VARCHAR(150)',
                'contact_person': 'VARCHAR(150)',
                'contact_email': 'VARCHAR(150)',
                'seating_chart': 'VARCHAR(100)',
                'meal_choice': 'VARCHAR(50)',
                'original_meal_choice': 'VARCHAR(50)',
                'status': 'VARCHAR(50) DEFAULT "未報到"',
                'checkin_time': 'DATETIME NULL',
                'proxy_name': 'VARCHAR(150)',
                'proxy_phone': 'VARCHAR(50)',
                'note': 'TEXT'
            }
        }
        for table, columns in alter_map.items():
            for col, definition in columns.items():
                try:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                except Exception as e:
                    if not _ignore_duplicate_column_error(e):
                        print(f"⚠️ 核心欄位檢查略過 {table}.{col}: {e}")

        # 如果 event_configs 是舊版只有 event_key 主鍵，補完欄位後不強制改主鍵，避免破壞現有資料。
        # 但查詢會使用 admin_user + event_key，因此舊資料會透過 default admin / 活動報到名單 被保留。
    conn.commit()


# ============================================================
# 【Dashboard 真實同步資料表保護】
# 確保議程、行業對照、企業展示資料真的可以寫入資料庫。
# ============================================================

def _ignore_duplicate_column_error(exc):
    msg = str(exc).lower()
    return 'duplicate column' in msg or 'duplicate column name' in msg or '1060' in msg


def ensure_dashboard_tables(conn):
    ensure_core_tables(conn)
    with conn.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_agenda (
                id INT AUTO_INCREMENT PRIMARY KEY,
                admin_user VARCHAR(100) NOT NULL,
                event_key VARCHAR(150) NOT NULL,
                time VARCHAR(50),
                event TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_agenda_event (admin_user, event_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS company_industry_mapping (
                id INT AUTO_INCREMENT PRIMARY KEY,
                admin_user VARCHAR(100) NOT NULL,
                event_key VARCHAR(150) NOT NULL,
                company_name VARCHAR(255) NOT NULL,
                industry VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_industry_event (admin_user, event_key),
                INDEX idx_industry_company (company_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_exhibitors (
                id INT AUTO_INCREMENT PRIMARY KEY,
                admin_user VARCHAR(100) NOT NULL,
                event_key VARCHAR(150) NOT NULL,
                company_name VARCHAR(255) NOT NULL,
                industry VARCHAR(100),
                logo TEXT,
                description TEXT,
                website TEXT,
                contact TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_exhibitor_event (admin_user, event_key),
                INDEX idx_exhibitor_company (company_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        for table, columns in {
            'event_exhibitors': {
                'website': 'TEXT',
                'contact': 'TEXT',
                'logo': 'TEXT',
                'description': 'TEXT',
                'industry': 'VARCHAR(100)'
            },
            'event_agenda': {
                'time': 'VARCHAR(50)',
                'event': 'TEXT'
            },
            'company_industry_mapping': {
                'industry': 'VARCHAR(100)'
            }
        }.items():
            for col, definition in columns.items():
                try:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                except Exception as e:
                    if not _ignore_duplicate_column_error(e):
                        print(f"⚠️ 欄位檢查略過 {table}.{col}: {e}")
    conn.commit()


def _clean_str(value):
    if value is None:
        return ''
    return str(value)


def _to_json_safe_rows(rows):
    safe = []
    for row in rows:
        item = {}
        for key, value in row.items():
            if isinstance(value, (datetime,)):
                item[key] = value.strftime('%Y-%m-%d %H:%M:%S')
            elif isinstance(value, timedelta):
                total_seconds = int(value.total_seconds())
                h = total_seconds // 3600
                m = (total_seconds % 3600) // 60
                item[key] = f"{h:02d}:{m:02d}"
            else:
                item[key] = value
        safe.append(item)
    return safe

# ============================================================
# 👑 【新增：議程 API】
# ============================================================

@app.route('/api/agenda', methods=['GET', 'POST'])
def handle_agenda():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_dashboard_tables(conn)
        if request.method == 'POST':
            if not session.get('admin_logged_in'):
                return jsonify({"success": False, "message": "未授權的操作"}), 403
            data = request.json or {}
            agenda_items = data.get('agenda', [])
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM event_agenda WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                for item in agenda_items:
                    time_text = _clean_str(item.get('time')).strip()
                    event_text = _clean_str(item.get('event')).strip()
                    if not time_text and not event_text:
                        continue
                    cursor.execute(
                        "INSERT INTO event_agenda (admin_user, event_key, time, event) VALUES (%s, %s, %s, %s)",
                        (admin_user, event_key, time_text, event_text)
                    )
            conn.commit()
            return jsonify({"success": True, "message": "議程已儲存"})

        with conn.cursor() as cursor:
            cursor.execute("SELECT id, time, event FROM event_agenda WHERE admin_user = %s AND event_key = %s ORDER BY id ASC", (admin_user, event_key))
            items = _to_json_safe_rows(cursor.fetchall())
        return jsonify({"success": True, "data": items})
    except Exception as e:
        print(f"❌ [議程 API 失敗]: {e}")
        return jsonify({"success": False, "message": str(e), "data": []}), 500
    finally:
        conn.close()


# ============================================================
# 👑 【新增：企業展示 & 行業統計 API】
# ============================================================

@app.route('/api/exhibitors', methods=['GET', 'POST'])
def handle_exhibitors():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_dashboard_tables(conn)
        if request.method == 'POST':
            if not session.get('admin_logged_in'):
                return jsonify({"success": False, "message": "未授權的操作"}), 403
            data = request.json or {}
            exhibitors = data.get('exhibitors', [])
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM event_exhibitors WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                for ex in exhibitors:
                    company_name = _clean_str(ex.get('name') or ex.get('company_name')).strip()
                    if not company_name:
                        continue
                    cursor.execute("""INSERT INTO event_exhibitors
                                      (admin_user, event_key, company_name, industry, logo, description, website, contact)
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                   (admin_user, event_key, company_name, _clean_str(ex.get('industry')).strip(),
                                    _clean_str(ex.get('logo') or '🏢').strip(), _clean_str(ex.get('description')).strip(),
                                    _clean_str(ex.get('website')).strip(), _clean_str(ex.get('contact')).strip()))
            conn.commit()
            return jsonify({"success": True, "message": "企業資訊已儲存到資料庫"})

        with conn.cursor() as cursor:
            # 1) 後台固定維護的企業展示資料
            cursor.execute("""SELECT id, company_name, industry, logo, description, website, contact
                              FROM event_exhibitors
                              WHERE admin_user = %s AND event_key = %s
                              ORDER BY id ASC""", (admin_user, event_key))
            raw_exhibitors = _to_json_safe_rows(cursor.fetchall())
            exhibitors = []
            for ex in raw_exhibitors:
                exhibitors.append({
                    "id": ex.get("id"),
                    "name": ex.get("company_name") or "",
                    "company_name": ex.get("company_name") or "",
                    "industry": ex.get("industry") or "未分類",
                    "logo": ex.get("logo") or "🏢",
                    "description": ex.get("description") or "",
                    "website": ex.get("website") or "",
                    "contact": ex.get("contact") or ""
                })

            # 2) 即使尚未填企業展示，也從 CSV 名單/報到資料抓出與會公司，避免 dashboard 空白。
            cursor.execute("""
                SELECT
                    MIN(r.id) AS id,
                    TRIM(r.company_name) AS company_name,
                    COALESCE(NULLIF(TRIM(m.industry), ''), NULLIF(TRIM(e.industry), ''), '未分類') AS industry,
                    COALESCE(NULLIF(TRIM(e.logo), ''), '🏢') AS logo,
                    COALESCE(NULLIF(TRIM(e.description), ''), '') AS description,
                    COALESCE(NULLIF(TRIM(e.website), ''), '') AS website,
                    COALESCE(NULLIF(TRIM(e.contact), ''), '') AS contact,
                    COUNT(*) AS people_count,
                    SUM(CASE WHEN r.status IN ('checked_in', '已報到', '替代') THEN 1 ELSE 0 END) AS checked_count
                FROM event_registrations r
                LEFT JOIN company_industry_mapping m
                    ON m.admin_user = r.admin_user
                   AND m.event_key = r.event_key
                   AND TRIM(m.company_name) = TRIM(r.company_name)
                LEFT JOIN event_exhibitors e
                    ON e.admin_user = r.admin_user
                   AND e.event_key = r.event_key
                   AND TRIM(e.company_name) = TRIM(r.company_name)
                WHERE r.admin_user = %s
                  AND r.event_key = %s
                  AND TRIM(COALESCE(r.company_name, '')) <> ''
                GROUP BY TRIM(r.company_name), industry, logo, description, website, contact
                ORDER BY checked_count DESC, people_count DESC, company_name ASC
            """, (admin_user, event_key))
            participating_companies = []
            for ex in _to_json_safe_rows(cursor.fetchall()):
                company_name = ex.get("company_name") or ""
                participating_companies.append({
                    "id": ex.get("id"),
                    "name": company_name,
                    "company_name": company_name,
                    "industry": ex.get("industry") or "未分類",
                    "logo": ex.get("logo") or "🏢",
                    "description": ex.get("description") or "",
                    "website": ex.get("website") or "",
                    "contact": ex.get("contact") or "",
                    "people_count": int(ex.get("people_count") or 0),
                    "checked_count": int(ex.get("checked_count") or 0)
                })

            def load_industry_stats(only_checked):
                status_sql = "AND r.status IN ('checked_in', '已報到', '替代')" if only_checked else ""
                cursor.execute(f"""
                    SELECT COALESCE(NULLIF(TRIM(m.industry), ''), NULLIF(TRIM(e.industry), ''), '未分類') AS industry,
                           COUNT(*) AS cnt
                    FROM event_registrations r
                    LEFT JOIN company_industry_mapping m
                        ON m.admin_user = r.admin_user
                       AND m.event_key = r.event_key
                       AND TRIM(m.company_name) = TRIM(r.company_name)
                    LEFT JOIN event_exhibitors e
                        ON e.admin_user = r.admin_user
                       AND e.event_key = r.event_key
                       AND TRIM(e.company_name) = TRIM(r.company_name)
                    WHERE r.admin_user = %s
                      AND r.event_key = %s
                      AND TRIM(COALESCE(r.company_name, '')) <> ''
                      {status_sql}
                    GROUP BY industry
                    ORDER BY cnt DESC
                """, (admin_user, event_key))
                return {row['industry'] or '未分類': int(row['cnt'] or 0) for row in cursor.fetchall() if int(row['cnt'] or 0) > 0}

            checked_stats = load_industry_stats(True)
            all_stats = load_industry_stats(False)
            industry_stats = checked_stats if checked_stats else all_stats

        return jsonify({
            "success": True,
            "exhibitors": exhibitors,
            "participating_companies": participating_companies,
            "industry_stats": industry_stats,
            "checked_industry_stats": checked_stats,
            "registered_industry_stats": all_stats
        })
    except Exception as e:
        print(f"❌ [企業/行業 API 失敗]: {e}")
        return jsonify({"success": False, "message": str(e), "exhibitors": [], "participating_companies": [], "industry_stats": {}}), 500
    finally:
        conn.close()


# ============================================================
# 👑 【新增：行業對照表 API】
# ============================================================

@app.route('/api/industry_mapping', methods=['GET', 'POST'])
def handle_industry_mapping():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_dashboard_tables(conn)
        if request.method == 'POST':
            if not session.get('admin_logged_in'):
                return jsonify({"success": False, "message": "未授權的操作"}), 403
            data = request.json or {}
            mappings = data.get('mappings', [])
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM company_industry_mapping WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                for mapping in mappings:
                    company_name = _clean_str(mapping.get('company') or mapping.get('company_name')).strip()
                    industry = _clean_str(mapping.get('industry')).strip()
                    if not company_name:
                        continue
                    cursor.execute("""INSERT INTO company_industry_mapping
                                      (admin_user, event_key, company_name, industry)
                                      VALUES (%s, %s, %s, %s)""",
                                   (admin_user, event_key, company_name, industry or '未分類'))
            conn.commit()
            return jsonify({"success": True, "message": "行業對照已儲存"})

        with conn.cursor() as cursor:
            cursor.execute("SELECT id, company_name, industry FROM company_industry_mapping WHERE admin_user = %s AND event_key = %s ORDER BY id ASC", (admin_user, event_key))
            mappings = _to_json_safe_rows(cursor.fetchall())
        return jsonify({"success": True, "data": mappings})
    except Exception as e:
        print(f"❌ [行業對照 API 失敗]: {e}")
        return jsonify({"success": False, "message": str(e), "data": []}), 500
    finally:
        conn.close()


# ============================================================
# 👑 【新增：AI 抓取公司資料 API】
# ============================================================

@app.route('/api/fetch_company_info', methods=['POST'])
def fetch_company_info():
    """使用 AI 或搜尋引擎抓取公司資料"""
    data = request.json
    company_name = data.get('company_name', '')
    
    # 目前先返回空殼，未來可整合 OpenAI API 或 Google Search API
    try:
        # 示例：可以調用 OpenAI API 生成公司簡介
        # response = openai.ChatCompletion.create(...)
        
        return jsonify({
            "success": True,
            "company_name": company_name,
            "description": f"[AI 生成] {company_name} 的公司簡介...",
            "industry": "未分類",
            "website": "",
            "contact": ""
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================
# 【原有 API - 保持不變】
# ============================================================

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_core_tables(conn)
        if request.method == 'POST':
            if not session.get('admin_logged_in'): return jsonify({"success": False, "message": "未授權的操作"}), 403
            payload = request.json
            new_sheet = payload.get("google_sheet_name")
            if new_sheet and new_sheet in session.get('allowed_sheets', []):
                session['current_admin_sheet'] = new_sheet
                event_key = new_sheet
            
            with conn.cursor() as cursor:
                sql_cfg = "REPLACE INTO event_configs (admin_user, event_key, show_meal_options, map_image_url, banner_image_url) VALUES (%s, %s, %s, %s, %s)"
                cursor.execute(sql_cfg, (admin_user, event_key, 1, payload.get("map_image_url", ""), payload.get("banner_image_url", "")))
                
                if "products" in payload:
                    cursor.execute("DELETE FROM event_products WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                    sql_prod = "INSERT INTO event_products (admin_user, event_key, name, image, category, description, link, is_gift) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    for p in payload["products"]:
                        cursor.execute(sql_prod, (admin_user, event_key, p.get("name", ""), p.get("image", ""), p.get("category", "課程"), p.get("description", ""), p.get("link", ""), 1 if p.get("isGift") else 0))
            conn.commit()
            return jsonify({"success": True, "message": "設定儲存成功"})

        config_data = {
            "show_meal_options": True, "google_sheet_name": event_key, "map_image_url": "", "banner_image_url": "", "products": [],
            "excel_columns": {"id": 1, "name": 1, "phone": 1, "company": 1, "email": 1, "qrCode": 1, "registeredAt": 1, "checkedInAt": 1, "status": 1, "meal": 1}
        }
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM event_configs WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
            cfg = cursor.fetchone()
            if cfg:
                config_data["show_meal_options"] = bool(cfg["show_meal_options"])
                config_data["map_image_url"] = cfg.get("map_image_url") or ""
                config_data["banner_image_url"] = cfg.get("banner_image_url") or ""
                
            cursor.execute("SELECT * FROM event_products WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
            prods = cursor.fetchall()
            for r in prods:
                config_data["products"].append({
                    "name": r["name"], "image": r["image"], "category": r["category"],
                    "description": r["description"], "link": r["link"], "isGift": bool(r["is_gift"])
                })
        return jsonify(config_data)
    except Exception as e: return jsonify({"success": False, "message": str(e)}), 500
    finally: conn.close()

@app.route('/api/search/<method>')
def search(method):
    admin_user, event_key = get_admin_and_event_context()
    q = request.args.get(method, "").strip()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if method == 'company':
                if len(q) < 2:
                    return jsonify({"success": True, "data": []})
                cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s AND company_name LIKE %s LIMIT 50", (admin_user, event_key, f"%{q}%"))
            elif method == 'phone':
                cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s AND (phone LIKE %s OR name LIKE %s)", (admin_user, event_key, f"%{q}%", f"%{q}%"))
            else:
                cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s AND name LIKE %s", (admin_user, event_key, f"%{q}%"))
            res = cursor.fetchall()
            for r in res:
                r['id'] = str(r['id'])
                r['company'] = r['company_name']
                r['meal'] = r['meal_choice']
                r['original_meal'] = r.get('original_meal_choice', r['meal_choice'])
                r['seat'] = r['seating_chart']
                r['status_display'] = '已報到' if r['status'] in ['checked_in', '已報到', '替代'] else '未報到'
            return jsonify({"success": True, "data": res})
    finally: conn.close()

@app.route('/api/checkin/<pid>', methods=['POST'])
def checkin(pid):
    admin_user, event_key = get_admin_and_event_context()
    data = request.json
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM event_registrations WHERE id = %s AND admin_user = %s", (pid, admin_user))
            user = cursor.fetchone()
            
            if not user:
                return jsonify({"success": False, "error": "user_not_found"}), 404
            
            if user['status'] in ['checked_in', '已報到', '替代']:
                return jsonify({"success": False, "error": "already_done", "data": {
                    "name": user['name'],
                    "company": user['company_name'],
                    "seat": user['seating_chart'],
                    "meal": user['meal_choice'],
                    "original_meal": user.get('original_meal_choice', user['meal_choice']),
                    "checkedInAt": user['checkin_time'].strftime('%H:%M:%S') if user['checkin_time'] else ""
                }}), 200
            
            is_original = data.get('is_original', True)
            meal_choice = user['meal_choice'] if is_original else data.get('meal', user['meal_choice'])
            status_val = 'checked_in' if is_original else '替代'
            
            original_meal = user.get('original_meal_choice', user['meal_choice'])
            
            cursor.execute(
                "UPDATE event_registrations SET checkin_time = %s, status = %s, meal_choice = %s, original_meal_choice = %s WHERE id = %s AND admin_user = %s",
                (datetime.now(), status_val, meal_choice, original_meal, pid, admin_user)
            )
            conn.commit()
            
            return jsonify({"success": True, "data": {
                "name": user['name'],
                "company": user['company_name'],
                "seat": user['seating_chart'],
                "meal": meal_choice,
                "original_meal": original_meal,
                "checkedInAt": datetime.now().strftime('%H:%M:%S')
            }})
    except Exception as e:
        print(f"❌ [報到失敗]: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/dashboard_stats')
def get_dashboard_stats():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_core_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, name, phone, company_name, seating_chart, status, checkin_time, meal_choice
                FROM event_registrations
                WHERE admin_user = %s AND event_key = %s
                ORDER BY id ASC
            """, (admin_user, event_key))
            rows = cursor.fetchall()

        def is_checked(row):
            return row.get('status') in ['checked_in', '已報到', '替代']

        def normalize_table(value):
            if value is None:
                return ''
            t = str(value).strip()
            if not t:
                return ''
            compact = t.replace(' ', '')
            zero_values = {'0', '第0桌', '第０桌', '0桌', '０桌', '第 0 桌'}
            if compact in zero_values:
                return ''
            return t

        def person_payload(row):
            return {
                "id": row.get("id"),
                "name": row.get("name") or "",
                "phone": row.get("phone") or "",
                "company": row.get("company_name") or "",
                "seat": row.get("seating_chart") or "",
                "meal": row.get("meal_choice") or "",
                "status": row.get("status") or "",
                "checked": is_checked(row),
                "checkin_time": row.get("checkin_time").strftime('%H:%M:%S') if row.get("checkin_time") else ""
            }

        total = len(rows)
        checked = [r for r in rows if is_checked(r)]

        original_meals = {}
        actual_meals = {}
        for r in rows:
            meal = r.get('meal_choice') or '未選擇'
            original_meals[meal] = original_meals.get(meal, 0) + 1
        for r in checked:
            meal = r.get('meal_choice') or '未選擇'
            actual_meals[meal] = actual_meals.get(meal, 0) + 1

        table_stats = {}
        for r in rows:
            table = normalize_table(r.get('seating_chart'))
            if not table:
                continue
            if table not in table_stats:
                table_stats[table] = {"total": 0, "checked": 0, "checked_people": [], "pending_people": []}
            table_stats[table]["total"] += 1
            if is_checked(r):
                table_stats[table]["checked"] += 1
                table_stats[table]["checked_people"].append(person_payload(r))
            else:
                table_stats[table]["pending_people"].append(person_payload(r))

        def table_sort_key(item):
            key = str(item[0])
            nums = re.findall(r'\d+', key)
            return (int(nums[0]) if nums else 999999, key)

        table_stats_formatted = []
        for table, v in sorted(table_stats.items(), key=table_sort_key):
            percent = round((v["checked"] / v["total"] * 100), 1) if v["total"] else 0
            table_stats_formatted.append({
                "table": table,
                "checked": v["checked"],
                "total": v["total"],
                "percent": percent,
                "checked_people": v["checked_people"],
                "pending_people": v["pending_people"]
            })

        logs = []
        for r in checked[:25]:
            logs.append({
                "name": r.get('name') or "",
                "time": r.get('checkin_time').strftime('%H:%M:%S') if r.get('checkin_time') else "",
                "company": r.get('company_name') or "",
                "meal": r.get('meal_choice') or ""
            })

        return jsonify({"success": True, "stats": {
            "total": total,
            "checked_in": len(checked),
            "not_checked_in": total - len(checked),
            "original_meals": original_meals,
            "actual_meals": actual_meals,
            "table_stats": table_stats_formatted,
            "logs": logs
        }})
    finally:
        conn.close()

()

@app.route('/api/registrations/add', methods=['POST'])
def add_registration():
    if not session.get('admin_logged_in'): return jsonify({"success": False, "message": "未授權"}), 403
    admin_user, event_key = get_admin_and_event_context()
    data = request.json
    conn = get_db_connection()
    try:
        meal = data.get('meal', '未選擇')
        with conn.cursor() as cursor:
            sql = """INSERT INTO event_registrations (admin_user, event_key, name, phone, company_name, seating_chart, status, checkin_time, meal_choice, original_meal_choice, note) 
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            cursor.execute(sql, (admin_user, event_key, data.get('name'), data.get('phone'), data.get('company'), data.get('seat', '現場安排'), '已報到', datetime.now(), meal, meal, '現場臨時報到'))
        conn.commit()
        return jsonify({"success": True})
    finally: conn.close()


@app.route('/api/health')
def api_health():
    return jsonify({
        "success": True,
        "status": "ok",
        "message": "server is running"
    })

@app.route('/api/db_check')
def api_db_check():
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 AS ok")
            row = cursor.fetchone()
        conn.close()
        return jsonify({
            "success": True,
            "database": "connected",
            "result": row
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "database": "error",
            "message": str(e)
        }), 500

@app.route('/api/user/info')
def api_user_info():
    if not session.get('admin_logged_in'):
        return jsonify({
            "success": False,
            "logged_in": False,
            "message": "尚未登入"
        }), 401

    username = session.get('username', 'admin')
    allowed_sheets = session.get('allowed_sheets', [])
    current_sheet = session.get('current_admin_sheet') or (allowed_sheets[0] if allowed_sheets else '活動報到名單')

    return jsonify({
        "success": True,
        "logged_in": True,
        "username": username,
        "allowed_sheets": allowed_sheets,
        "current_sheet": current_sheet
    })

@app.route('/api/sheets/list')
def api_sheets_list():
    """
    後台「MySQL 雲端活動資料庫場次」下拉選單使用。
    來源：Aiven MySQL 的 admins.allowed_events。
    這不是 Google Sheets，也不是假資料；每次開後台會重新讀 DB，DBeaver 改 allowed_events 後重新整理即可看到。
    """
    if not session.get('admin_logged_in'):
        return jsonify({
            "success": False,
            "message": "尚未登入",
            "sheets": []
        }), 401

    username = session.get('username', 'admin')
    conn = None
    try:
        conn = get_db_connection()
        ensure_core_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("SELECT allowed_events FROM admins WHERE username = %s", (username,))
            row = cursor.fetchone()

        allowed_text = (row or {}).get('allowed_events') or ''
        sheets = [s.strip() for s in allowed_text.split(',') if s.strip()]
        if not sheets:
            sheets = session.get('allowed_sheets', []) or ['活動報到名單']

        session['allowed_sheets'] = sheets
        if session.get('current_admin_sheet') not in sheets:
            session['current_admin_sheet'] = sheets[0]

        return jsonify({
            "success": True,
            "source": "aiven_mysql_admins_allowed_events",
            "username": username,
            "sheets": sheets,
            "current_sheet": session.get('current_admin_sheet')
        })
    except Exception as e:
        fallback = session.get('allowed_sheets', [])
        if fallback:
            return jsonify({
                "success": True,
                "source": "session_fallback",
                "username": username,
                "sheets": fallback,
                "current_sheet": session.get('current_admin_sheet') or fallback[0],
                "warning": str(e)
            })
        return jsonify({
            "success": False,
            "message": f"MySQL 場次列表讀取失敗：{e}",
            "sheets": []
        }), 500
    finally:
        if conn:
            conn.close()


@app.route('/admin')
def admin_page():
    if not session.get('admin_logged_in'): return send_from_directory('.', 'login.html')
    return send_from_directory('.', 'admin.html')

@app.route('/')
def index(): return send_from_directory('.', '活動報到系統.html')


@app.route('/api/sheets/export_csv', methods=['GET'])
def export_csv():
    if not session.get('admin_logged_in'):
        return jsonify({"success": False, "message": "未授權的操作"}), 403
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_core_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT name, phone, company_name, email, region, training_level, seating_chart,
                       status, checkin_time, meal_choice, note
                FROM event_registrations
                WHERE admin_user = %s AND event_key = %s
                ORDER BY id ASC
            """, (admin_user, event_key))
            rows = cursor.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["姓名", "手機", "單位/公司", "電子郵件", "地區", "職階", "桌號/座位", "報到狀態", "報到時間", "餐點選擇", "備註"])
        for r in rows:
            writer.writerow([
                r.get('name', ''), r.get('phone', ''), r.get('company_name', ''), r.get('email', ''),
                r.get('region', ''), r.get('training_level', ''), r.get('seating_chart', ''), r.get('status', ''),
                r.get('checkin_time').strftime('%Y-%m-%d %H:%M:%S') if r.get('checkin_time') else '未報到',
                r.get('meal_choice', ''), r.get('note', '')
            ])
        response = app.make_response(output.getvalue().encode('utf-8-sig'))
        response.headers["Content-Disposition"] = f"attachment; filename={event_key}_export.csv"
        response.headers["Content-type"] = "text/csv; charset=utf-8-sig"
        return response
    finally:
        conn.close()

@app.route('/api/sheets/upload_csv', methods=['POST'])
def upload_csv_to_sheet():
    if not session.get('admin_logged_in'):
        return jsonify({"success": False, "message": "未授權的操作，請重新登入後再上傳 CSV。"}), 403
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "找不到檔案，請重新選擇 CSV。"}), 400

    file = request.files['file']
    admin_user, current_event = get_admin_and_event_context()
    event_key = (request.form.get('sheet') or request.args.get('sheet') or os.path.splitext(file.filename)[0] or current_event or '活動報到名單').strip()
    # 避免 Windows 檔名帶副檔名或空白造成錯誤
    event_key = event_key.replace('.csv', '').replace('.CSV', '').strip() or '活動報到名單'

    def decode_csv(file_bytes):
        for enc in ['utf-8-sig', 'utf-8', 'big5', 'cp950']:
            try:
                return file_bytes.decode(enc), enc
            except Exception:
                pass
        return file_bytes.decode('utf-8', errors='ignore'), 'utf-8-ignore'

    def clean_val(v):
        if v is None:
            return ''
        return str(v).strip().replace('\ufeff', '').replace('，', ',')

    try:
        file_bytes = file.stream.read()
        csv_text, encoding_used = decode_csv(file_bytes)
        stream = io.StringIO(csv_text, newline=None)
        sample = csv_text[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',\t;')
            rows = list(csv.reader(stream, dialect))
        except Exception:
            stream.seek(0)
            rows = list(csv.reader(stream))

        rows = [[clean_val(c) for c in row] for row in rows if any(clean_val(c) for c in row)]
        if not rows:
            return jsonify({"success": False, "message": "CSV 檔案為空，請確認內容。"}), 400

        # 直式表格救援：如果標題都在第一欄，就轉置
        first_col = [row[0] for row in rows[:12] if row]
        header_keywords = ['姓名', '手機', '電話', 'Email', 'email', '公司', '單位']
        if sum(any(k in c for k in header_keywords) for c in first_col) >= 2 and len(rows[0]) <= 3:
            max_len = max(len(r) for r in rows)
            padded = [r + [''] * (max_len - len(r)) for r in rows]
            rows = list(map(list, zip(*padded)))

        mapping_targets = {
            'region': ['區', '梯次', '地區', '組別', '分區'],
            'training_level': ['階', '職階', '等級', '職稱'],
            'company_name': ['公司', '單位', '機關', '部門', '行號', '社團'],
            'contract_period': ['合約', '期間', '合約期'],
            'participant_count': ['人數', '名額', '數量'],
            'name': ['姓名', '旅客', '學員', '名字', '人員'],
            'phone': ['手機', '電話', '聯絡電話', '行動電話', '號碼'],
            'email': ['電子郵件', 'email', '郵件', '信箱', '電郵'],
            'contact_person': ['窗口', '聯絡人', '負責人'],
            'contact_email': ['窗口信箱', '聯絡人信箱', '經辦email'],
            'note': ['備註', '說明'],
            'seating_chart': ['桌號', '座位', '座次', '桌次'],
            'meal_choice': ['餐', '便當', '飲食', '葷素']
        }
        field_indices = {k: -1 for k in mapping_targets}
        header_row_idx = -1
        for r_idx, row in enumerate(rows[:20]):
            lowered = [c.lower() for c in row]
            has_name = any(('姓名' in c or '學員' in c or '旅客' in c or '名字' in c) for c in lowered)
            has_phone = any(('手機' in c or '電話' in c or '聯絡' in c or '號碼' in c) for c in lowered)
            has_email = any(('email' in c or '郵件' in c or '信箱' in c) for c in lowered)
            if has_name and (has_phone or has_email or any('公司' in c or '單位' in c for c in lowered)):
                header_row_idx = r_idx
                for c_idx, cell in enumerate(lowered):
                    for key, keywords in mapping_targets.items():
                        if field_indices[key] == -1 and any(kw.lower() in cell for kw in keywords):
                            field_indices[key] = c_idx
                break

        if header_row_idx == -1 or field_indices['name'] == -1:
            return jsonify({"success": False, "message": "CSV 辨識失敗：找不到『姓名』標題列。請確認第一列附近有姓名/手機/公司等欄位。"}), 400

        conn = get_db_connection()
        try:
            ensure_core_tables(conn)
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM event_registrations WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))

                insert_sql = """
                    INSERT INTO event_registrations
                    (admin_user, event_key, region, training_level, company_name, contract_period, participant_count,
                     name, job_title, phone, email, contact_person, contact_email, note, seating_chart,
                     status, meal_choice, original_meal_choice)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                last_values = {k: '' for k in mapping_targets}
                success_count = 0
                skipped_count = 0
                for row in rows[header_row_idx + 1:]:
                    if not any(row):
                        continue
                    data = {}
                    for key, idx in field_indices.items():
                        val = clean_val(row[idx]) if idx != -1 and idx < len(row) else ''
                        # 公司/地區/桌號常有合併儲存格，允許往下延續；姓名/手機/email 不延續，避免重複人名。
                        if not val and key in ['region', 'training_level', 'company_name', 'contract_period', 'participant_count', 'seating_chart', 'meal_choice']:
                            val = last_values.get(key, '')
                        elif val:
                            last_values[key] = val
                        data[key] = val

                    name = data.get('name', '').strip()
                    if not name or name in ['姓名', '學員', '旅客']:
                        skipped_count += 1
                        continue
                    p_count_raw = (data.get('participant_count') or '1').replace(' ', '')
                    participant_count = int(p_count_raw) if p_count_raw.isdigit() else 1
                    meal = data.get('meal_choice') or '未選擇'
                    cursor.execute(insert_sql, (
                        admin_user, event_key,
                        data.get('region', ''), data.get('training_level', ''), data.get('company_name', ''),
                        data.get('contract_period', ''), participant_count, name, data.get('training_level', ''),
                        data.get('phone', ''), data.get('email', ''), data.get('contact_person', ''),
                        data.get('contact_email', ''), data.get('note', ''), data.get('seating_chart', ''),
                        '未報到', meal, meal
                    ))
                    success_count += 1

                cursor.execute("SELECT allowed_events FROM admins WHERE username = %s", (admin_user,))
                admin_row = cursor.fetchone()
                allowed = [s.strip() for s in (admin_row or {}).get('allowed_events', '').split(',') if s.strip()]
                if not allowed:
                    allowed = ['活動報到名單']
                if event_key not in allowed:
                    allowed.append(event_key)
                cursor.execute("UPDATE admins SET allowed_events = %s, current_event = %s WHERE username = %s", (','.join(allowed), event_key, admin_user))
                session['allowed_sheets'] = allowed
                session['current_admin_sheet'] = event_key
            conn.commit()
            return jsonify({
                "success": True,
                "message": f"CSV 匯入完成：已寫入『{event_key}』共 {success_count} 筆，略過 {skipped_count} 列。",
                "event_key": event_key,
                "count": success_count,
                "encoding": encoding_used
            })
        except Exception as e:
            conn.rollback()
            print(f"❌ [CSV寫入資料庫失敗]: {e}")
            return jsonify({"success": False, "message": f"資料庫寫入失敗：{e}"}), 500
        finally:
            conn.close()
    except Exception as e:
        print(f"❌ [CSV解析失敗]: {e}")
        return jsonify({"success": False, "message": f"CSV 解析失敗：{e}"}), 500

@app.route('/api/login', methods=['POST'])
def admin_login():
    data = request.json
    u, p = data.get('username'), data.get('password')
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM admins WHERE username = %s AND password = %s", (u, p))
            admin = cursor.fetchone()
            if admin:
                session['admin_logged_in'] = True
                session['username'] = admin['username']
                allowed = [s.strip() for s in admin['allowed_events'].split(',') if s.strip()]
                session['allowed_sheets'] = allowed or ["活動報到名單"]
                session['current_admin_sheet'] = session['allowed_sheets'][0]
                return jsonify({"success": True})
        return jsonify({"success": False, "message": "帳密錯誤"}), 401
    finally: conn.close()

@app.route('/api/logout')
def logout():
    session.clear()
    return redirect('/login.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
