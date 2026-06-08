import os, json, time, requests, csv, io
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
import pymysql
import pymysql.cursors

app = Flask(__name__, static_folder='.', static_url_path='')
# 多租戶安全金鑰設定
app.secret_key = os.environ.get("SECRET_KEY", "rcsa_ark_secure_key_20260508_multitenant") 
CORS(app)

# ============================================================
# 【MySQL 資料庫連線設定】
# ============================================================
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

# 👑 【管理員宇宙與活動標籤動態切換器】
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

def upload_image_to_free_pool(base64_str):
    if not base64_str or not str(base64_str).startswith("data:image/"): return base64_str
    try:
        base64_data = base64_str.split(",")[1] if "," in base64_str else base64_str
        res = requests.post("https://api.imgbb.com/1/upload", data={"key": "2b3149867c4b69cdbda90ea8fbd52ec3", "image": base64_data}, timeout=15).json()
        if res.get("success"): return res["data"]["url"]
    except Exception as e: print(f"❌ [圖床失敗]: {e}")
    return base64_str

# ============================================================
# 【API 路由區】
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
            
            map_image_url = upload_image_to_free_pool(payload.get("map_image_url", ""))
            show_meal_options = 1 if payload.get("show_meal_options", True) else 0
            
            with conn.cursor() as cursor:
                sql_cfg = "REPLACE INTO event_configs (admin_user, event_key, show_meal_options, map_image_url) VALUES (%s, %s, %s, %s)"
                cursor.execute(sql_cfg, (admin_user, event_key, show_meal_options, map_image_url))
                
                if "products" in payload:
                    cursor.execute("DELETE FROM event_products WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                    sql_prod = "INSERT INTO event_products (admin_user, event_key, name, image, category, description, link, is_gift) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    for p in payload["products"]:
                        p_img = upload_image_to_free_pool(p.get("image", ""))
                        cursor.execute(sql_prod, (admin_user, event_key, p.get("name", ""), p_img, p.get("category", "課程"), p.get("description", ""), p.get("link", ""), 1 if p.get("isGift") else 0))
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
                config_data["banner_image_url"] = cfg.get("banner_image_url", "")
                
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

@app.route('/api/sheets/list', methods=['GET'])
def list_available_sheets():
    if not session.get('admin_logged_in'): return jsonify({"success": False, "message": "未授權的操作"}), 403
    allowed = session.get('allowed_sheets', ["活動報到名單"])
    return jsonify({"success": True, "sheets": allowed})

@app.route('/api/sheets/export_csv', methods=['GET'])
def export_csv():
    if not session.get('admin_logged_in'): return jsonify({"success": False, "message": "未授權的操作"}), 403
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
            rows = cursor.fetchall()
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["姓名", "手機", "單位/公司", "電子郵件", "地區", "職階", "桌號/座位", "報到狀態", "報到時間", "原報名餐食", "實際餐食", "備註"])
        
        for r in rows:
            writer.writerow([
                r['name'], r['phone'], r['company_name'], r['email'], r['region'], 
                r['training_level'], r['seating_chart'], r['status'], 
                r['checkin_time'].strftime('%Y-%m-%d %H:%M:%S') if r['checkin_time'] else "未報到",
                r.get('original_meal_choice', r['meal_choice']), r['meal_choice'], r['note']
            ])
        
        response = app.make_response(output.getvalue().encode('utf-8-sig'))
        response.headers["Content-Disposition"] = f"attachment; filename={event_key}_export.csv"
        response.headers["Content-type"] = "text/csv; charset=utf-8-sig"
        return response
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
    if not session.get('admin_logged_in'): return jsonify({"success": False}), 403
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
                if table not in table_stats:
                    table_stats[table] = {"total": 10, "checked": 0}
                if r['status'] in ['checked_in', 'AA報到', '替代', '已報到']:
                    table_stats[table]["checked"] += 1
        
        sorted_tables = sorted(table_stats.items())
        table_stats_formatted = [{"table": k, "checked": v["checked"], "total": v["total"]} for k, v in sorted_tables]
        logs = [{"name": r['name'], "time": r['checkin_time'].strftime('%H:%M:%S') if r['checkin_time'] else "", "company": r['company_name'], "meal": r['meal_choice']} for r in checked[:25]]
        
        return jsonify({"success": True, "stats": {
            "total": total, "checked_in": len(checked), "not_checked_in": total - len(checked),
            "original_meals": original_meals, "actual_meals": actual_meals,
            "table_stats": table_stats_formatted, "logs": logs
        }})
    finally: conn.close()

@app.route('/api/user/info')
def get_user_info():
    if not session.get('admin_logged_in'): return jsonify({"success": False, "message": "未登入"}), 401
    return jsonify({"success": True, "username": session.get('username', '管理員')})

@app.route('/api/current_sheet', methods=['GET'])
def get_current_sheet():
    admin_user = request.args.get('admin')
    if not admin_user: return jsonify({"success": False, "error": "Missing admin parameter"}), 400
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_event FROM admins WHERE username = %s", (admin_user,))
            data = cursor.fetchone()
        conn.close()
        if data: return jsonify({"success": True, "current_sheet": data['current_event']})
        return jsonify({"success": False, "error": "User not found"}), 404
    except Exception as e: return jsonify({"success": False, "error": str(e)}), 500

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
# 【👑 新增：前台大會資訊儀表板與後台手動配置 API】
# ============================================================

@app.route('/api/public_dashboard', methods=['GET'])
def public_dashboard():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # 1. 獲取手動設定的大會議程
            cursor.execute("SELECT * FROM event_agenda WHERE admin_user=%s AND event_key=%s ORDER BY time_str ASC", (admin_user, event_key))
            agenda = cursor.fetchall()

            # 2. 獲取圓餅圖：依據已報到的「不重複公司 (DISTINCT)」手動分類占比
            sql_industry = """
                SELECT 
                    COALESCE(c.industry_category, '尚未分類') as category, 
                    COUNT(DISTINCT r.company_name) as count 
                FROM event_registrations r
                LEFT JOIN company_industries c 
                    ON r.company_name = c.company_name 
                    AND r.admin_user = c.admin_user 
                    AND r.event_key = c.event_key
                WHERE r.status IN ('checked_in', '已報到', '替代')
                  AND r.admin_user = %s 
                  AND r.event_key = %s
                  AND r.company_name IS NOT NULL
                  AND r.company_name != ''
                GROUP BY COALESCE(c.industry_category, '尚未分類')
            """
            cursor.execute(sql_industry, (admin_user, event_key))
            stats = cursor.fetchall()
            
        return jsonify({"success": True, "agenda": agenda, "industry_stats": stats})
    finally: conn.close()

# 大會議程後台配置端
@app.route('/api/agenda', methods=['GET', 'POST'])
def manage_agenda():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if request.method == 'POST':
                data = request.json
                cursor.execute("INSERT INTO event_agenda (admin_user, event_key, time_str, task_desc) VALUES (%s, %s, %s, %s)", 
                               (admin_user, event_key, data['time_str'], data['task_desc']))
                conn.commit()
                return jsonify({"success": True})
            else:
                cursor.execute("SELECT * FROM event_agenda WHERE admin_user=%s AND event_key=%s ORDER BY time_str ASC", (admin_user, event_key))
                return jsonify({"success": True, "data": cursor.fetchall()})
    finally: conn.close()

@app.route('/api/agenda/<id>', methods=['DELETE'])
def delete_agenda(id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM event_agenda WHERE id=%s", (id,))
        conn.commit()
        return jsonify({"success": True})
    finally: conn.close()

# 行業分類後台配置端
@app.route('/api/industries', methods=['GET', 'POST'])
def manage_industries():
    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if request.method == 'POST':
                data = request.json
                # 如果有重複绑定的公司，先進行清除覆寫
                cursor.execute("DELETE FROM company_industries WHERE admin_user=%s AND event_key=%s AND company_name=%s", (admin_user, event_key, data['company_name']))
                cursor.execute("INSERT INTO company_industries (admin_user, event_key, company_name, industry_category) VALUES (%s, %s, %s, %s)", 
                               (admin_user, event_key, data['company_name'], data['industry_category']))
                conn.commit()
                return jsonify({"success": True})
            else:
                cursor.execute("SELECT * FROM company_industries WHERE admin_user=%s AND event_key=%s", (admin_user, event_key))
                return jsonify({"success": True, "data": cursor.fetchall()})
    finally: conn.close()

@app.route('/api/industries/<id>', methods=['DELETE'])
def delete_industry(id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM company_industries WHERE id=%s", (id,))
        conn.commit()
        return jsonify({"success": True})
    finally: conn.close()

# ============================================================
# 【基礎頁面與登入基礎路由】
# ============================================================

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
    s
@app.route('/dashboard')
def dashboard_page():
    # 會場投影專用獨立路由
    return send_from_directory('.', 'dashboard.html')

def auto_init_mysql_tables():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM admins")
            if cursor.fetchone()['cnt'] == 0:
                cursor.execute("INSERT INTO admins (username, password, allowed_events) VALUES (%s, %s, %s)", ("admin", "admin123", "活動報到名單"))
                conn.commit()
                print("💡 [MySQL 初始化] 已建立預設 admin 帳號")
            
            # 👑 自動建立手動議程資料表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_agenda (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    admin_user VARCHAR(100),
                    event_key VARCHAR(100),
                    time_str VARCHAR(50),
                    task_desc VARCHAR(255)
                )
            """)
            
            # 👑 自動建立手動行業對照分類資料表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS company_industries (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    admin_user VARCHAR(100),
                    event_key VARCHAR(100),
                    company_name VARCHAR(255),
                    industry_category VARCHAR(100)
                )
            """)
            conn.commit()
            print("💡 [MySQL 初始化] 大會資訊與行業資料表檢測/建置完成")
    except Exception as e: print(f"❌ [MySQL 初始化失敗]: {e}")
    finally: conn.close()

if __name__ == '__main__':
    auto_init_mysql_tables()
    app.run(host='0.0.0.0', port=10000)
