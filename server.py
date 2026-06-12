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



CHECKED_STATUSES = ('checked_in', '已報到', '替代')

def ensure_support_tables():
    """確保 Dashboard/後台設定使用的資料表存在；不改動原有報到資料。"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_agenda (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    admin_user VARCHAR(50) NOT NULL,
                    event_key VARCHAR(100) NOT NULL,
                    time VARCHAR(50),
                    event TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_agenda_scope (admin_user, event_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS company_industry_mapping (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    admin_user VARCHAR(50) NOT NULL,
                    event_key VARCHAR(100) NOT NULL,
                    company_name VARCHAR(255) NOT NULL,
                    industry VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_industry_scope (admin_user, event_key),
                    INDEX idx_industry_company (company_name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_exhibitors (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    admin_user VARCHAR(50) NOT NULL,
                    event_key VARCHAR(100) NOT NULL,
                    company_name VARCHAR(255) NOT NULL,
                    industry VARCHAR(100),
                    logo VARCHAR(255),
                    description TEXT,
                    website TEXT,
                    contact TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_exhibitor_scope (admin_user, event_key),
                    INDEX idx_exhibitor_company (company_name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
        conn.commit()
    finally:
        conn.close()

def normalize_table_label(value):
    """過濾空值、第 0 桌、0、無桌號，避免前台顯示第 0 桌。"""
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    compact = raw.replace(' ', '').replace('第', '').replace('桌', '').replace('號', '')
    if compact in ('0', '０', '無', '無桌', '現場安排', '未安排', 'none', 'None', 'NULL', 'null', '-'):
        return ""
    return raw

def checked_status_sql():
    return "('checked_in','已報到','替代')"


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
# 👑 【新增：議程 API】
# ============================================================

@app.route('/api/agenda', methods=['GET', 'POST'])
def handle_agenda():
    ensure_support_tables()
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    
    try:
        if request.method == 'POST':
            if not session.get('admin_logged_in'): return jsonify({"success": False}), 403
            data = request.json
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM event_agenda WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                for item in data.get('agenda', []):
                    cursor.execute("INSERT INTO event_agenda (admin_user, event_key, time, event) VALUES (%s, %s, %s, %s)",
                                   (admin_user, event_key, item['time'], item['event']))
            conn.commit()
            return jsonify({"success": True})
        
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM event_agenda WHERE admin_user = %s AND event_key = %s ORDER BY time", (admin_user, event_key))
            items = cursor.fetchall()
        return jsonify({"success": True, "data": items})
    finally: conn.close()

# ============================================================
# 👑 【新增：企業展示 & 行業統計 API】
# ============================================================

@app.route('/api/exhibitors', methods=['GET', 'POST'])
def handle_exhibitors():
    ensure_support_tables()
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    
    try:
        if request.method == 'POST':
            if not session.get('admin_logged_in'):
                return jsonify({"success": False, "message": "未授權的操作"}), 403
            data = request.json or {}
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM event_exhibitors WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                for ex in data.get('exhibitors', []):
                    company_name = (ex.get('name') or ex.get('company_name') or '').strip()
                    if not company_name:
                        continue
                    cursor.execute("""INSERT INTO event_exhibitors 
                                      (admin_user, event_key, company_name, industry, logo, description, website, contact)
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                   (admin_user, event_key, company_name, ex.get('industry'), ex.get('logo') or '🏢',
                                    ex.get('description'), ex.get('website'), ex.get('contact')))
            conn.commit()
            return jsonify({"success": True})
        
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, company_name, company_name AS name, industry, logo, description, website, contact FROM event_exhibitors WHERE admin_user = %s AND event_key = %s ORDER BY id", (admin_user, event_key))
            exhibitors = cursor.fetchall()
            
            # 依資料庫目前名單即時統計公司行業比例：優先使用行業對照表，其次企業展示，最後未分類。
            cursor.execute("""
                SELECT DISTINCT TRIM(r.company_name) AS company_name,
                       COALESCE(NULLIF(TRIM(m.industry), ''), NULLIF(TRIM(e.industry), ''), '未分類') AS industry
                FROM event_registrations r
                LEFT JOIN company_industry_mapping m
                    ON TRIM(r.company_name) = TRIM(m.company_name)
                   AND r.admin_user = m.admin_user
                   AND r.event_key = m.event_key
                LEFT JOIN event_exhibitors e
                    ON TRIM(r.company_name) = TRIM(e.company_name)
                   AND r.admin_user = e.admin_user
                   AND r.event_key = e.event_key
                WHERE r.admin_user = %s
                  AND r.event_key = %s
                  AND TRIM(IFNULL(r.company_name, '')) <> ''
            """, (admin_user, event_key))
            companies = cursor.fetchall()
            industry_stats = {}
            for row in companies:
                industry = row.get('industry') or '未分類'
                industry_stats[industry] = industry_stats.get(industry, 0) + 1
        
        return jsonify({
            "success": True,
            "exhibitors": exhibitors,
            "industry_stats": industry_stats
        })
    finally:
        conn.close()

# ============================================================
# 👑 【新增：行業對照表 API】
# ============================================================

@app.route('/api/industry_mapping', methods=['GET', 'POST'])
def handle_industry_mapping():
    ensure_support_tables()
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    
    try:
        if request.method == 'POST':
            if not session.get('admin_logged_in'): return jsonify({"success": False}), 403
            data = request.json
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM company_industry_mapping WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                for mapping in data.get('mappings', []):
                    cursor.execute("""INSERT INTO company_industry_mapping 
                                      (admin_user, event_key, company_name, industry)
                                      VALUES (%s, %s, %s, %s)""",
                                   (admin_user, event_key, mapping['company'], mapping['industry']))
            conn.commit()
            return jsonify({"success": True})
        
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM company_industry_mapping WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
            mappings = cursor.fetchall()
        return jsonify({"success": True, "data": mappings})
    finally: conn.close()

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
            
            cursor.execute(
                "UPDATE event_registrations SET checkin_time = %s, status = %s, meal_choice = %s, original_meal_choice = %s WHERE id = %s AND admin_user = %s AND event_key = %s",
                (datetime.now(), status_val, meal_choice, original_meal, pid, admin_user, event_key)
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
    # 後台登入時使用 session；投影頁如帶 admin/sheet 也能讀取即時統計。
    if not session.get('admin_logged_in') and not request.args.get('admin'):
        return jsonify({"success": False, "message": "未授權"}), 403
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
            rows = cursor.fetchall()
        
        total = len(rows)
        checked = [r for r in rows if r.get('status') in CHECKED_STATUSES]
        
        original_meals = {}
        actual_meals = {}
        for r in rows:
            orig = r.get('original_meal_choice') or r.get('meal_choice') or '未選擇'
            original_meals[orig] = original_meals.get(orig, 0) + 1
        for r in checked:
            meal = r.get('meal_choice') or '未選擇'
            actual_meals[meal] = actual_meals.get(meal, 0) + 1
        
        table_stats = {}
        for r in rows:
            table = normalize_table_label(r.get('seating_chart'))
            if not table:
                continue
            if table not in table_stats:
                table_stats[table] = {"total": 0, "checked": 0}
            table_stats[table]["total"] += 1
            if r.get('status') in CHECKED_STATUSES:
                table_stats[table]["checked"] += 1
        
        def sort_key(item):
            label = str(item[0])
            digits = ''.join(ch for ch in label if ch.isdigit())
            return (int(digits) if digits else 999999, label)
        sorted_tables = sorted(table_stats.items(), key=sort_key)
        table_stats_formatted = [
            {"table": k, "checked": v["checked"], "total": v["total"], "percent": round((v["checked"] / v["total"]) * 100) if v["total"] else 0}
            for k, v in sorted_tables
        ]
        
        checked_sorted = sorted(checked, key=lambda r: r.get('checkin_time') or datetime.min, reverse=True)
        logs = [{
            "name": r.get('name') or '',
            "time": r['checkin_time'].strftime('%H:%M:%S') if r.get('checkin_time') else "",
            "company": r.get('company_name') or '',
            "meal": r.get('meal_choice') or ''
        } for r in checked_sorted[:25]]
        
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


@app.route('/api/user/info')
def user_info():
    if not session.get('admin_logged_in'):
        return jsonify({"success": False, "message": "未登入"}), 401
    return jsonify({
        "success": True,
        "username": session.get('username', 'admin'),
        "allowed_sheets": session.get('allowed_sheets', []),
        "current_sheet": session.get('current_admin_sheet', '活動報到名單')
    })

@app.route('/api/sheets/list', methods=['GET'])
def list_available_sheets():
    if not session.get('admin_logged_in'):
        return jsonify({"success": False, "message": "未授權的操作"}), 403
    allowed = session.get('allowed_sheets') or ["活動報到名單"]
    return jsonify({"success": True, "sheets": allowed})

@app.route('/api/sheets/export_csv', methods=['GET'])
def export_csv():
    if not session.get('admin_logged_in'):
        return jsonify({"success": False, "message": "未授權的操作"}), 403
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s ORDER BY id", (admin_user, event_key))
            rows = cursor.fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["姓名", "手機", "單位/公司", "電子郵件", "桌號/座位", "報到狀態", "報到時間", "餐點選擇", "備註"])
        for r in rows:
            writer.writerow([
                r.get('name',''), r.get('phone',''), r.get('company_name',''), r.get('email',''), r.get('seating_chart',''),
                r.get('status',''), r['checkin_time'].strftime('%Y-%m-%d %H:%M:%S') if r.get('checkin_time') else '',
                r.get('meal_choice',''), r.get('note','')
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
        return jsonify({"success": False, "message": "未授權的操作"}), 403
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "找不到檔案"}), 400
    file = request.files['file']
    admin_user = session.get('username', 'admin')
    event_key = os.path.splitext(file.filename)[0] or '活動報到名單'
    try:
        file_bytes = file.stream.read()
        try:
            csv_text = file_bytes.decode('utf-8-sig')
        except Exception:
            csv_text = file_bytes.decode('big5', errors='ignore')
        rows = list(csv.reader(io.StringIO(csv_text, newline=None)))
        if not rows:
            return jsonify({"success": False, "message": "檔案為空"}), 400
        header_idx = -1
        headers = []
        for i, row in enumerate(rows[:15]):
            normalized = [str(c).strip().lower() for c in row]
            if any('姓名' in c or 'name' in c for c in normalized) and any('手機' in c or '電話' in c or 'phone' in c for c in normalized):
                header_idx = i
                headers = normalized
                break
        if header_idx == -1:
            return jsonify({"success": False, "message": "辨識失敗：找不到姓名與手機欄位"}), 400
        def find_col(keys):
            for idx, h in enumerate(headers):
                if any(k in h for k in keys):
                    return idx
            return -1
        idx_name = find_col(['姓名','name','學員','旅客'])
        idx_phone = find_col(['手機','電話','phone','行動'])
        idx_company = find_col(['公司','單位','company','機關'])
        idx_email = find_col(['email','郵件','信箱'])
        idx_seat = find_col(['桌號','座位','桌次','座次'])
        idx_meal = find_col(['餐','便當','葷素','meal'])
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM event_registrations WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                sql = """INSERT INTO event_registrations
                         (admin_user, event_key, name, phone, company_name, email, seating_chart, status, meal_choice, original_meal_choice, note)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
                count = 0
                for row in rows[header_idx+1:]:
                    def val(idx):
                        return row[idx].strip() if idx != -1 and idx < len(row) else ''
                    name = val(idx_name)
                    phone = val(idx_phone)
                    if not name:
                        continue
                    meal = val(idx_meal) or '未選擇'
                    seat = normalize_table_label(val(idx_seat))
                    cursor.execute(sql, (admin_user, event_key, name, phone, val(idx_company), val(idx_email), seat, '未報到', meal, meal, 'CSV匯入'))
                    count += 1
                allowed = session.get('allowed_sheets', [])
                if event_key not in allowed:
                    allowed.append(event_key)
                    session['allowed_sheets'] = allowed
                    cursor.execute("UPDATE admins SET allowed_events = %s WHERE username = %s", (','.join(allowed), admin_user))
                session['current_admin_sheet'] = event_key
            conn.commit()
            return jsonify({"success": True, "message": f"已匯入 {count} 筆名單到「{event_key}」", "sheet": event_key})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

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
    ensure_support_tables()
    app.run(host='0.0.0.0', port=10000)
