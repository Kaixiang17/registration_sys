import os, json, time, requests, csv, io
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
        ssl={"ssl": {}}, cursorclass=pymysql.cursors.DictCursor
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


# ============================================================
# 【Dashboard 真實同步資料表保護】
# 確保議程、行業對照、企業展示資料真的可以寫入資料庫。
# ============================================================

def _ignore_duplicate_column_error(exc):
    msg = str(exc).lower()
    return 'duplicate column' in msg or 'duplicate column name' in msg or '1060' in msg


def ensure_dashboard_tables(conn):
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
            return jsonify({"success": True, "message": "企業資訊已儲存"})

        with conn.cursor() as cursor:
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

            # 已報到者優先；如果目前尚無已報到資料，改用整份名單，避免投影頁空白。
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
                    WHERE r.admin_user = %s AND r.event_key = %s {status_sql}
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
            "industry_stats": industry_stats,
            "checked_industry_stats": checked_stats,
            "registered_industry_stats": all_stats
        })
    except Exception as e:
        print(f"❌ [企業/行業 API 失敗]: {e}")
        return jsonify({"success": False, "message": str(e), "exhibitors": [], "industry_stats": {}}), 500
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
        if request.method == 'POST':
            if not session.get('admin_logged_in'): return jsonify({"success": False, "message": "未授權的操作"}), 403
            payload = request.json
            new_sheet = payload.get("google_sheet_name")
            if new_sheet and new_sheet in session.get('allowed_sheets', []):
                session['current_admin_sheet'] = new_sheet
                event_key = new_sheet
            
            with conn.cursor() as cursor:
                sql_cfg = "REPLACE INTO event_configs (admin_user, event_key, show_meal_options, map_image_url) VALUES (%s, %s, %s, %s)"
                cursor.execute(sql_cfg, (admin_user, event_key, 1, payload.get("map_image_url", "")))
                
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
                config_data["map_image_url"] = cfg["map_image_url"]
                
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
    finally: conn.close()

@app.route('/api/dashboard_stats')
def get_dashboard_stats():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
            rows = cursor.fetchall()
        
        total = len(rows)
        checked = [r for r in rows if r['status'] in ['checked_in', '已報到', '替代']]
        
        original_meals = {}
        actual_meals = {}
        for r in rows:
            orig = r.get('original_meal_choice', r['meal_choice'])
            original_meals[orig] = original_meals.get(orig, 0) + 1
        
        for r in checked:
            meal = r['meal_choice']
            actual_meals[meal] = actual_meals.get(meal, 0) + 1
        
        table_stats = {}
        for r in rows:
            table = r['seating_chart']
            if table:
                if str(table).strip() in ['', '0', '第0桌', '第 0 桌']:
                    continue
                if table not in table_stats:
                    table_stats[table] = {"total": 0, "checked": 0}
                table_stats[table]["total"] += 1
                if r['status'] in ['checked_in', '已報到', '替代']:
                    table_stats[table]["checked"] += 1
        
        sorted_tables = sorted(table_stats.items())
        table_stats_formatted = [{"table": k, "checked": v["checked"], "total": v["total"]} for k, v in sorted_tables]
        
        logs = [{"name": r['name'], "time": r['checkin_time'].strftime('%H:%M:%S') if r['checkin_time'] else "", "company": r['company_name'], "meal": r['meal_choice']} for r in checked[:25]]
        
        return jsonify({"success": True, "stats": {
            "total": total,
            "checked_in": len(checked),
            "not_checked_in": total - len(checked),
            "original_meals": original_meals,
            "actual_meals": actual_meals,
            "table_stats": table_stats_formatted,
            "logs": logs
        }})
    finally: conn.close()

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

@app.route('/admin')
def admin_page():
    if not session.get('admin_logged_in'): return send_from_directory('.', 'login.html')
    return send_from_directory('.', 'admin.html')

@app.route('/')
def index(): return send_from_directory('.', '活動報到系統.html')

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
