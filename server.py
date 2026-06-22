import os, json, time, requests, csv, io, re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
import pymysql
import pymysql.cursors

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get("SECRET_KEY", "rcsa_ark_secure_key_20260508_multitenant")
CORS(app)

# Railway MySQL 優先；保留 DB_* 作為本機/Aiven fallback。
DB_HOST = os.getenv("MYSQLHOST") or os.getenv("DB_HOST")
DB_USER = os.getenv("MYSQLUSER") or os.getenv("DB_USER")
DB_PASSWORD = os.getenv("MYSQLPASSWORD") or os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("MYSQLDATABASE") or os.getenv("DB_NAME", "railway")
DB_PORT = int(os.getenv("MYSQLPORT") or os.getenv("DB_PORT", "3306"))

_CORE_TABLES_READY = False

def get_db_connection():
    if not DB_HOST or not DB_USER or not DB_PASSWORD:
        raise RuntimeError("資料庫環境變數缺少：請確認 MYSQLHOST / MYSQLUSER / MYSQLPASSWORD / MYSQLDATABASE / MYSQLPORT 已設定在 Railway web service。")
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=8,
        read_timeout=20,
        write_timeout=20,
        autocommit=False
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



def ensure_core_tables(conn, force=False):
    """
    保護 Railway/MySQL 核心資料表。
    admin.html 一進後台會先呼叫 /api/config；如果 event_configs / event_products / event_registrations
    不存在或缺少 admin_user、event_key，就會顯示「設定載入失敗」。
    """
    global _CORE_TABLES_READY
    if _CORE_TABLES_READY and not force:
        return

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

        # 舊資料表補欄位：舊版 schema，所以不能只靠 CREATE TABLE IF NOT EXISTS。
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


        # Railway 新資料庫常是空的；建立預設管理員，避免登入/場次下拉抓不到。
        try:
            cursor.execute("""
                INSERT INTO admins (username, password, allowed_events, current_event)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE password = VALUES(password), allowed_events = VALUES(allowed_events), current_event = VALUES(current_event)
            """, (os.getenv('ADMIN_USERNAME', 'admin'), os.getenv('ADMIN_PASSWORD', 'admin123'), os.getenv('ADMIN_DEFAULT_EVENTS', '活動報到名單'), os.getenv('ADMIN_DEFAULT_EVENT', '活動報到名單')))
        except Exception as e:
            print(f"⚠️ 預設 admin 建立略過: {e}")

        # 如果 event_configs 是舊版只有 event_key 主鍵，補完欄位後不強制改主鍵，避免破壞現有資料。
        # 但查詢會使用 admin_user + event_key，因此舊資料會透過 default admin / 活動報到名單 被保留。
    conn.commit()
    _CORE_TABLES_READY = True


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
            cursor.execute("SELECT * FROM event_registrations WHERE id = %s AND admin_user = %s AND event_key = %s", (pid, admin_user, event_key))
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
            
            proxy_info = data.get('proxy_info') or {}
            proxy_name = _clean_str(proxy_info.get('name')).strip()
            proxy_phone = _clean_str(proxy_info.get('phone')).strip()
            cursor.execute(
                """
                UPDATE event_registrations
                SET checkin_time = %s,
                    status = %s,
                    meal_choice = %s,
                    original_meal_choice = %s,
                    proxy_name = %s,
                    proxy_phone = %s
                WHERE id = %s AND admin_user = %s AND event_key = %s
                """,
                (datetime.now(), status_val, meal_choice, original_meal,
                 proxy_name if not is_original else None,
                 proxy_phone if not is_original else None,
                 pid, admin_user, event_key)
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
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status IN ('checked_in', '已報到', '替代') THEN 1 ELSE 0 END) AS checked_in
                FROM event_registrations
                WHERE admin_user = %s AND event_key = %s
            """, (admin_user, event_key))
            summary = cursor.fetchone() or {}

            cursor.execute("""
                SELECT
                    seating_chart AS table_name,
                    COUNT(*) AS total,
                    SUM(CASE WHEN status IN ('checked_in', '已報到', '替代') THEN 1 ELSE 0 END) AS checked
                FROM event_registrations
                WHERE admin_user = %s
                  AND event_key = %s
                  AND seating_chart IS NOT NULL
                  AND TRIM(seating_chart) NOT IN ('', '0', '第0桌', '第 0 桌', '第０桌', '0桌', '０桌')
                GROUP BY seating_chart
                ORDER BY CAST(REGEXP_REPLACE(seating_chart, '[^0-9]', '') AS UNSIGNED), seating_chart
            """, (admin_user, event_key))
            table_rows = cursor.fetchall()

            cursor.execute("""
                SELECT name, checkin_time, company_name, meal_choice
                FROM event_registrations
                WHERE admin_user = %s
                  AND event_key = %s
                  AND status IN ('checked_in', '已報到', '替代')
                ORDER BY checkin_time DESC, id DESC
                LIMIT 25
            """, (admin_user, event_key))
            checked_logs = cursor.fetchall()

        total = int(summary.get("total") or 0)
        checked_in = int(summary.get("checked_in") or 0)

        table_stats_formatted = []
        for r in table_rows:
            table = r.get("table_name") or ""
            total_table = int(r.get("total") or 0)
            checked_table = int(r.get("checked") or 0)
            percent = round((checked_table / total_table * 100), 1) if total_table else 0
            table_stats_formatted.append({
                "table": table,
                "checked": checked_table,
                "total": total_table,
                "percent": percent
            })

        logs = [{
            "name": r.get('name') or "",
            "time": r.get('checkin_time').strftime('%H:%M:%S') if r.get('checkin_time') else "",
            "company": r.get('company_name') or "",
            "meal": r.get('meal_choice') or ""
        } for r in checked_logs]

        return jsonify({"success": True, "stats": {
            "total": total,
            "checked_in": checked_in,
            "not_checked_in": total - checked_in,
            "table_stats": table_stats_formatted,
            "logs": logs
        }})
    finally:
        conn.close()

@app.route('/api/table_detail')
def api_table_detail():
    admin_user, event_key = get_admin_and_event_context()
    table = (request.args.get('table') or '').strip()
    if not table:
        return jsonify({"success": False, "message": "缺少桌號"}), 400

    conn = get_db_connection()
    try:
        ensure_core_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, name, phone, company_name, seating_chart, status, checkin_time, meal_choice
                FROM event_registrations
                WHERE admin_user = %s AND event_key = %s AND seating_chart = %s
                ORDER BY
                    CASE WHEN status IN ('checked_in', '已報到', '替代') THEN 0 ELSE 1 END,
                    checkin_time DESC,
                    id ASC
            """, (admin_user, event_key, table))
            rows = cursor.fetchall()

        def is_checked(row):
            return row.get('status') in ['checked_in', '已報到', '替代']

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

        checked_people = [person_payload(r) for r in rows if is_checked(r)]
        pending_people = [person_payload(r) for r in rows if not is_checked(r)]

        return jsonify({
            "success": True,
            "table": table,
            "total": len(rows),
            "checked": len(checked_people),
            "pending": len(pending_people),
            "checked_people": checked_people,
            "pending_people": pending_people
        })
    finally:
        conn.close()


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



# ============================================================
# 【智慧論壇 / 產業大會 Navigator 設定 API】
# ============================================================

EXPERIENCE_CONFIG_DEFAULTS = {
    "event_title": "2026 全球面對面",
    "event_subtitle": "世代共榮的數位聚合",
    "event_date_start": "2026/06/01",
    "event_date_end": "2026/08/XX",
    "brand_name": "智慧方舟 SMART WISDOM ARK",
    "logo_url": "",
    "gift_title": "方舟物資艙",
    "gift_image_url": "",
    "gift_description": "傳承伴手禮核心內涵，守護碼 / 禮品說明手冊。可於後台設定完整圖文說明。",
    "gift_enabled": True,
    "video_title": "核心引擎啟動",
    "video_url": "",
    "video_embed_enabled": True,
    "video_enabled": True,
    "flow_title": "大會時空座標",
    "flow_image_url": "",
    "flow_description": "09:30 - 17:00 航程時間軸，建議上傳流程視覺圖。",
    "flow_enabled": True,
    "projection_title": "世代共榮的數位聚合",
    "projection_subtitle": "DIGITAL CONVERGENCE FOR GENERATIONAL PROSPERITY"
}

DEFAULT_SCHEDULE = [
    {"time": "09:30", "title": "報到啟航", "description": "領航員迎賓報到"},
    {"time": "10:00", "title": "策略羅盤", "description": "大師專題演講"},
    {"time": "12:00", "title": "方舟盛宴", "description": "產業能量餐敘"},
    {"time": "14:00", "title": "引擎啟動", "description": "戰略操盤與新品發表"},
    {"time": "15:00", "title": "跨世會談", "description": "世代交鋒論壇"}
]


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "啟用")


def _youtube_embed_url(url):
    url = (url or '').strip()
    if not url:
        return ''
    match = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{6,})', url)
    if match:
        return f"https://www.youtube.com/embed/{match.group(1)}"
    return url


def ensure_experience_tables(conn):
    """擴充現有 event_configs / event_agenda，不破壞原報到資料。"""
    ensure_dashboard_tables(conn)
    config_columns = {
        'event_title': 'VARCHAR(255)',
        'event_subtitle': 'VARCHAR(255)',
        'event_date_start': 'VARCHAR(50)',
        'event_date_end': 'VARCHAR(50)',
        'brand_name': 'VARCHAR(255)',
        'logo_url': 'LONGTEXT',
        'gift_title': 'VARCHAR(255)',
        'gift_image_url': 'LONGTEXT',
        'gift_description': 'LONGTEXT',
        'gift_enabled': 'BOOLEAN DEFAULT TRUE',
        'video_title': 'VARCHAR(255)',
        'video_url': 'LONGTEXT',
        'video_embed_enabled': 'BOOLEAN DEFAULT TRUE',
        'video_enabled': 'BOOLEAN DEFAULT TRUE',
        'flow_title': 'VARCHAR(255)',
        'flow_image_url': 'LONGTEXT',
        'flow_description': 'LONGTEXT',
        'flow_enabled': 'BOOLEAN DEFAULT TRUE',
        'projection_title': 'VARCHAR(255)',
        'projection_subtitle': 'VARCHAR(255)'
    }
    agenda_columns = {
        'title': 'VARCHAR(255)',
        'description': 'TEXT',
        'sort_order': 'INT DEFAULT 0'
    }
    with conn.cursor() as cursor:
        for col, definition in config_columns.items():
            try:
                cursor.execute(f"ALTER TABLE event_configs ADD COLUMN {col} {definition}")
            except Exception as e:
                if not _ignore_duplicate_column_error(e):
                    print(f"⚠️ event_configs.{col} 欄位檢查略過: {e}")
        for col, definition in agenda_columns.items():
            try:
                cursor.execute(f"ALTER TABLE event_agenda ADD COLUMN {col} {definition}")
            except Exception as e:
                if not _ignore_duplicate_column_error(e):
                    print(f"⚠️ event_agenda.{col} 欄位檢查略過: {e}")
    conn.commit()


def _event_config_from_row(row, admin_user, event_key):
    data = dict(EXPERIENCE_CONFIG_DEFAULTS)
    if row:
        for k in data.keys():
            if k in row and row.get(k) is not None:
                data[k] = row.get(k)
        # 舊設定相容：若沒有新 banner/logo，就仍回傳舊圖欄位。
        data['map_image_url'] = row.get('map_image_url') or ''
        data['banner_image_url'] = row.get('banner_image_url') or ''
    else:
        data['map_image_url'] = ''
        data['banner_image_url'] = ''
    for key in ['gift_enabled', 'video_embed_enabled', 'video_enabled', 'flow_enabled']:
        data[key] = _as_bool(data.get(key), True)
    data['admin_user'] = admin_user
    data['event_key'] = event_key
    data['google_sheet_name'] = event_key
    data['video_embed_url'] = _youtube_embed_url(data.get('video_url')) if data.get('video_embed_enabled') else data.get('video_url', '')
    return data


def _load_event_config(conn, admin_user, event_key):
    ensure_experience_tables(conn)
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM event_configs WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
        row = cursor.fetchone()
    return _event_config_from_row(row, admin_user, event_key)


@app.route('/api/event-config', methods=['GET'])
def api_event_config():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        return jsonify({"success": True, "config": _load_event_config(conn, admin_user, event_key)})
    except Exception as e:
        print(f"❌ [event-config 讀取失敗]: {e}")
        return jsonify({"success": False, "message": str(e), "config": _event_config_from_row({}, admin_user, event_key)}), 500
    finally:
        conn.close()


@app.route('/api/admin/event-config', methods=['PUT', 'POST'])
def api_admin_event_config():
    if not session.get('admin_logged_in'):
        return jsonify({"success": False, "message": "尚未登入"}), 401
    admin_user, event_key = get_admin_and_event_context()
    payload = request.get_json(silent=True) or {}
    conn = get_db_connection()
    try:
        ensure_experience_tables(conn)
        current = _load_event_config(conn, admin_user, event_key)
        data = dict(current)
        for key in EXPERIENCE_CONFIG_DEFAULTS.keys():
            if key in payload:
                data[key] = payload.get(key)
        data['gift_enabled'] = 1 if _as_bool(data.get('gift_enabled'), True) else 0
        data['video_embed_enabled'] = 1 if _as_bool(data.get('video_embed_enabled'), True) else 0
        data['video_enabled'] = 1 if _as_bool(data.get('video_enabled'), True) else 0
        data['flow_enabled'] = 1 if _as_bool(data.get('flow_enabled'), True) else 0
        map_image_url = payload.get('map_image_url', current.get('map_image_url', ''))
        banner_image_url = payload.get('banner_image_url', current.get('banner_image_url', ''))

        cols = [
            'admin_user', 'event_key', 'show_meal_options', 'map_image_url', 'banner_image_url',
            'event_title', 'event_subtitle', 'event_date_start', 'event_date_end', 'brand_name', 'logo_url',
            'gift_title', 'gift_image_url', 'gift_description', 'gift_enabled',
            'video_title', 'video_url', 'video_embed_enabled', 'video_enabled',
            'flow_title', 'flow_image_url', 'flow_description', 'flow_enabled',
            'projection_title', 'projection_subtitle'
        ]
        values = {
            'admin_user': admin_user,
            'event_key': event_key,
            'show_meal_options': 1,
            'map_image_url': map_image_url or '',
            'banner_image_url': banner_image_url or '',
            **data
        }
        placeholders = ', '.join(['%s'] * len(cols))
        update_clause = ', '.join([f"{c}=VALUES({c})" for c in cols if c not in ('admin_user', 'event_key')])
        sql = f"""
            INSERT INTO event_configs ({', '.join(cols)})
            VALUES ({placeholders})
            ON DUPLICATE KEY UPDATE {update_clause}
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, [values.get(c) for c in cols])
        conn.commit()
        return jsonify({"success": True, "message": "活動 Navigator 設定已儲存", "config": _load_event_config(conn, admin_user, event_key)})
    except Exception as e:
        conn.rollback()
        print(f"❌ [event-config 儲存失敗]: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/schedule', methods=['GET'])
def api_schedule():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        ensure_experience_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, time, title, description, event, sort_order
                FROM event_agenda
                WHERE admin_user = %s AND event_key = %s
                ORDER BY sort_order ASC, id ASC
            """, (admin_user, event_key))
            rows = _to_json_safe_rows(cursor.fetchall())
        if not rows:
            return jsonify({"success": True, "schedule": DEFAULT_SCHEDULE})
        schedule = []
        for i, row in enumerate(rows):
            title = row.get('title') or row.get('event') or ''
            desc = row.get('description') or ''
            if not desc and '：' in title:
                title, desc = title.split('：', 1)
            schedule.append({"id": row.get('id'), "time": row.get('time') or '', "title": title, "description": desc, "sort_order": row.get('sort_order') or i})
        return jsonify({"success": True, "schedule": schedule})
    except Exception as e:
        print(f"❌ [schedule 讀取失敗]: {e}")
        return jsonify({"success": False, "message": str(e), "schedule": DEFAULT_SCHEDULE}), 500
    finally:
        conn.close()


@app.route('/api/admin/schedule', methods=['PUT', 'POST'])
def api_admin_schedule():
    if not session.get('admin_logged_in'):
        return jsonify({"success": False, "message": "尚未登入"}), 401
    admin_user, event_key = get_admin_and_event_context()
    payload = request.get_json(silent=True) or {}
    schedule = payload.get('schedule') or payload.get('agenda') or []
    conn = get_db_connection()
    try:
        ensure_experience_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM event_agenda WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
            for idx, item in enumerate(schedule):
                time_text = _clean_str(item.get('time')).strip()
                title = _clean_str(item.get('title') or item.get('event')).strip()
                description = _clean_str(item.get('description')).strip()
                if not time_text and not title and not description:
                    continue
                event_text = f"{title}：{description}" if description else title
                cursor.execute("""
                    INSERT INTO event_agenda (admin_user, event_key, time, event, title, description, sort_order)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (admin_user, event_key, time_text, event_text, title, description, int(item.get('sort_order') or idx)))
        conn.commit()
        return jsonify({"success": True, "message": "流程時間軸已儲存"})
    except Exception as e:
        conn.rollback()
        print(f"❌ [schedule 儲存失敗]: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/current_sheet')
def api_current_sheet():
    admin_user, event_key = get_admin_and_event_context()
    return jsonify({"success": True, "admin": admin_user, "sheet": event_key, "current_sheet": event_key})



@app.route('/api/bootstrap_db')
def api_bootstrap_db():
    try:
        conn = get_db_connection()
        try:
            ensure_dashboard_tables(conn)
            ensure_experience_tables(conn)
            ensure_core_tables(conn, force=True)
            return jsonify({"success": True, "message": "Railway MySQL 核心資料表已檢查/建立完成"})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


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


@app.route('/api/session/sheet', methods=['POST'])
def api_session_sheet():
    """
    快速切換目前作用中的活動場次。
    只更新 session / admins.current_event，不重寫 event_configs，不刪 products，不 reload 整個後台。
    """
    if not session.get('admin_logged_in'):
        return jsonify({"success": False, "message": "尚未登入"}), 401

    payload = request.get_json(silent=True) or {}
    sheet = (payload.get('sheet') or request.form.get('sheet') or '').strip()
    username = session.get('username', 'admin')
    allowed = session.get('allowed_sheets', [])

    if not allowed:
        conn = None
        try:
            conn = get_db_connection()
            ensure_core_tables(conn)
            with conn.cursor() as cursor:
                cursor.execute("SELECT allowed_events FROM admins WHERE username = %s", (username,))
                row = cursor.fetchone()
            allowed_text = (row or {}).get('allowed_events') or ''
            allowed = [s.strip() for s in allowed_text.split(',') if s.strip()]
            session['allowed_sheets'] = allowed
        finally:
            if conn:
                conn.close()

    if not sheet:
        return jsonify({"success": False, "message": "缺少場次名稱"}), 400
    if allowed and sheet not in allowed:
        return jsonify({"success": False, "message": "此帳號沒有這個活動場次權限"}), 403

    session['current_admin_sheet'] = sheet

    # 寫回 MySQL，讓重新整理後仍記住目前場次；若 current_event 欄位不存在也不影響切換。
    conn = None
    try:
        conn = get_db_connection()
        ensure_core_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("UPDATE admins SET current_event = %s WHERE username = %s", (sheet, username))
        conn.commit()
    except Exception as e:
        print(f"⚠️ current_event 寫回失敗，不影響 session 切換: {e}")
    finally:
        if conn:
            conn.close()

    return jsonify({
        "success": True,
        "username": username,
        "current_sheet": sheet,
        "sheets": allowed
    })


@app.route('/api/sheets/list')
def api_sheets_list():
    """
    後台「MySQL 雲端活動資料庫場次」下拉選單使用。
    來源：Railway MySQL 的 admins.allowed_events。
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
            cursor.execute("SELECT allowed_events, current_event FROM admins WHERE username = %s", (username,))
            row = cursor.fetchone()

        allowed_text = (row or {}).get('allowed_events') or ''
        sheets = [s.strip() for s in allowed_text.split(',') if s.strip()]
        if not sheets:
            sheets = session.get('allowed_sheets', []) or ['活動報到名單']

        session['allowed_sheets'] = sheets
        db_current = ((row or {}).get('current_event') or '').strip()
        if db_current in sheets:
            session['current_admin_sheet'] = db_current
        elif session.get('current_admin_sheet') not in sheets:
            session['current_admin_sheet'] = sheets[0]

        return jsonify({
            "success": True,
            "source": "railway_mysql_admins_allowed_events",
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



@app.route('/dashboard')
def dashboard_page():
    return send_from_directory('.', 'dashboard.html')

@app.route('/projection')
def projection_page():
    return send_from_directory('.', 'dashboard.html')


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
    """
    後台登入 API。
    修正重點：
    1. 任何錯誤都回 JSON，不再只丟 Flask 500 HTML。
    2. username/password 自動 strip，避免資料庫或輸入框有空白導致登入失敗。
    3. 登入前先確保 admins/event_configs 等核心表存在。
    4. 回傳 debug_message，方便 Railway Logs 之外也能看出錯點。
    """
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        u = (data.get('username') or '').strip()
        p = (data.get('password') or '').strip()

        if not u or not p:
            return jsonify({"success": False, "message": "請輸入帳號與密碼"}), 400

        conn = get_db_connection()
        ensure_core_tables(conn)

        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, username, password, allowed_events, current_event
                FROM admins
                WHERE TRIM(username) = %s AND TRIM(password) = %s
                LIMIT 1
            """, (u, p))
            admin = cursor.fetchone()

        if not admin:
            return jsonify({"success": False, "message": "帳密錯誤"}), 401

        allowed = [
            s.strip()
            for s in (admin.get('allowed_events') or '活動報到名單').split(',')
            if s.strip()
        ] or ["活動報到名單"]

        current_event = (admin.get('current_event') or '').strip()
        if current_event not in allowed:
            current_event = allowed[0]

        session['admin_logged_in'] = True
        session['username'] = admin['username']
        session['allowed_sheets'] = allowed
        session['current_admin_sheet'] = current_event

        return jsonify({
            "success": True,
            "username": admin['username'],
            "allowed_sheets": allowed,
            "current_sheet": current_event
        })
    except Exception as e:
        print(f"❌ [登入 API 失敗]: {e}")
        return jsonify({
            "success": False,
            "message": f"登入 API 失敗：{e}"
        }), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/register', methods=['POST'])
def admin_register():
    """
    login.html 已經有註冊頁籤會呼叫 /api/register。
    原本 server.py 沒有這支 API，會導致註冊永遠失敗。
    """
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        username = (data.get('username') or '').strip()
        password = (data.get('password') or '').strip()
        allowed_events = (data.get('allowed_events') or '活動報到名單').strip()
        invite_code = (data.get('invite_code') or '').strip()

        required_invite = os.getenv("REGISTER_INVITE_CODE", "").strip()
        if required_invite and invite_code != required_invite:
            return jsonify({"success": False, "message": "邀請碼錯誤"}), 403

        if len(username) < 3:
            return jsonify({"success": False, "message": "帳號至少 3 個字元"}), 400
        if len(password) < 6:
            return jsonify({"success": False, "message": "密碼至少 6 碼"}), 400

        conn = get_db_connection()
        ensure_core_tables(conn)

        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM admins WHERE username = %s", (username,))
            if cursor.fetchone():
                return jsonify({"success": False, "message": "帳號已存在"}), 409

            cursor.execute("""
                INSERT INTO admins (username, password, allowed_events, current_event)
                VALUES (%s, %s, %s, %s)
            """, (username, password, allowed_events, allowed_events.split(',')[0].strip() or '活動報到名單'))

        conn.commit()
        return jsonify({"success": True, "message": "管理員建立成功"})
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ [註冊 API 失敗]: {e}")
        return jsonify({"success": False, "message": f"註冊 API 失敗：{e}"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/debug_login')
def api_debug_login():
    """
    臨時檢查 admins 表用。確認成功後可以刪掉。
    不回傳明文密碼，只回傳密碼長度。
    """
    conn = None
    try:
        conn = get_db_connection()
        ensure_core_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT username,
                       LENGTH(password) AS password_len,
                       allowed_events,
                       current_event
                FROM admins
                ORDER BY id ASC
            """)
            rows = cursor.fetchall()
        return jsonify({"success": True, "admins": rows})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/logout')
def logout():
    session.clear()
    return redirect('/login.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
