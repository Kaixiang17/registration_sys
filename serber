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
# 【MySQL 資料庫連線設定 - 👑
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
        writer.writerow(["姓名", "手機", "單位/公司", "電子郵件", "地區", "職階", "桌號/座位", "報到狀態", "報到時間", "餐點選擇", "備註"])
        
        for r in rows:
            writer.writerow([
                r['name'], r['phone'], r['company_name'], r['email'], r['region'], 
                r['training_level'], r['seating_chart'], r['status'], 
                r['checkin_time'].strftime('%Y-%m-%d %H:%M:%S') if r['checkin_time'] else "未報到",
                r['meal_choice'], r['note']
            ])
        
        response = app.make_response(output.getvalue().encode('utf-8-sig'))
        response.headers["Content-Disposition"] = f"attachment; filename={event_key}_export.csv"
        response.headers["Content-type"] = "text/csv; charset=utf-8-sig"
        return response
    finally: conn.close()

# 🚀 【百毒不侵、動態欄位智慧校準對齊核心】
@app.route('/api/sheets/upload_csv', methods=['POST'])
def upload_csv_to_sheet():
    if not session.get('admin_logged_in'): return jsonify({"success": False, "message": "未授權的操作"}), 403
    if 'file' not in request.files: return jsonify({"success": False, "message": "找不到檔案"}), 400
    file = request.files['file']
    admin_user, _ = get_admin_and_event_context()
    event_key = os.path.splitext(file.filename)[0] 
    
    try:
        file_bytes = file.stream.read()
        try: csv_text = file_bytes.decode("UTF-8")
        except: csv_text = file_bytes.decode("big5", errors="ignore")
            
        stream = io.StringIO(csv_text, newline=None)
        rows = list(csv.reader(stream))
        if not rows: return jsonify({"success": False, "message": "檔案為空"}), 400

        # 🔄 【智慧轉向偵測：直式 vs 橫式】
        is_horizontal = False
        potential_headers = ["姓名", "手機", "電話", "名單", "旅客", "學員"]
        for col_idx in range(min(5, len(rows[0]) if rows else 0)):
            col_content = [str(rows[r_idx][col_idx]).strip() for r_idx in range(min(10, len(rows))) if col_idx < len(rows[r_idx])]
            if any(any(kw in cell for kw in potential_headers) for cell in col_content):
                is_horizontal = True
                break
        
        if is_horizontal:
            rows = list(map(list, zip(*rows)))

        mapping_targets = {
            "region": ["區", "梯次", "地區", "組別", "分區"], 
            "training_level": ["階", "職階", "等級", "職稱"],
            "company_name": ["公司", "單位", "機關", "部門", "行號", "社團"], 
            "contract_period": ["合約", "期間", "合約期"],
            "participant_count": ["人數", "名額", "數量"], 
            "name": ["姓名", "旅客", "學員", "名字", "人員"], 
            "phone": ["手機", "電話", "聯絡電話", "行動電話"],
            "email": ["電子郵件", "email", "郵件", "信箱", "信箱地址", "電郵"], 
            "contact_person": ["窗口", "聯絡人", "負責人"],
            "contact_email": ["窗口信箱", "聯絡人信箱", "經辦email"],
            "note": ["備註", "說明"],
            "seating_chart": ["桌號", "座位", "座次", "桌次"], 
            "meal_choice": ["餐", "便當", "飲食", "葷素"]
        }
        
        field_indices = {k: -1 for k in mapping_targets.keys()}
        header_row_idx = -1

        # 👑 👑 👑 修正縮排與標頭探測核心
        for r_idx in range(min(15, len(rows))):
            row = [str(cell).strip().lower() for cell in rows[r_idx]]

            has_name = any("姓名" in cell or "學員" in cell or "旅客" in cell or "名字" in cell for cell in row)
            has_phone = any("手機" in cell or "電話" in cell or "聯絡" in cell or "號碼" in cell for cell in row)
            has_email = any("email" in cell or "郵件" in cell or "信箱" in cell for cell in row)

            if (has_name and has_phone) or (has_name and has_email):
                header_row_idx = r_idx
                for c_idx, cell in enumerate(row):
                    for field_key, keywords in mapping_targets.items():
                        if any(kw in cell for kw in keywords) and field_indices[field_key] == -1:
                            field_indices[field_key] = c_idx
                break

        if header_row_idx == -1 or field_indices["name"] == -1:
            missing = "姓名" if field_indices["name"] == -1 else "標題列"
            return jsonify({"success": False, "message": f"🤖 辨識失敗：找不到『{missing}』欄位。"}), 400

        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM event_registrations WHERE admin_user = %s AND event_key = %s", (admin_user, event_key))
                
                # 🎯 已完整對齊 MySQL 資料表現有的 21 個精準欄位結構
                sql = """INSERT INTO event_registrations (admin_user, event_key, region, training_level, company_name, contract_period, participant_count, name, job_title, phone, email, contact_person, contact_email, note, seating_chart, status, meal_choice) 
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
                
                last_values = {k: "" for k in field_indices.keys()}
                non_name_keywords = ["基層", "主管", "經理", "總監", "秘書", "訓練", "階層", "人數", "數量", "合計"]
                
                def clean_val(v):
                    if not v: return ""
                    return v.strip().replace('?', '').replace('，', '').replace(',', '')

                success_count = 0
                for row in rows[header_row_idx + 1:]:
                    if not any(row): continue
                    
                    # 1. Forward Fill 智慧合併儲存格遞補
                    current_row_data = {}
                    for key, idx in field_indices.items():
                        val = row[idx].strip() if (idx != -1 and idx < len(row)) else ""
                        if not val:
                            val = last_values[key]
                        else:
                            last_values[key] = val
                        current_row_data[key] = val

                    name = clean_val(current_row_data.get("name"))
                    phone = clean_val(current_row_data.get("phone"))

                    # 2. 錯位姓名救援
                    if not name:
                        p_idx = field_indices.get("participant_count")
                        if p_idx != -1 and p_idx < len(row):
                            p_val = clean_val(row[p_idx])
                            if 2 <= len(p_val) <= 5 and all('\u4e00' <= char <= '\u9fff' for char in p_val):
                                if not any(k in p_val for k in non_name_keywords):
                                    name = p_val
                    
                    # 3. Email 智慧突擊救援
                    email = clean_val(current_row_data.get("email", ""))
                    if not email:
                        for cell in row:
                            if "@" in cell and "." in cell:
                                email = cell.strip()
                                break

                    if name:
                        p_count_raw = current_row_data.get("participant_count", "1").replace(" ", "")
                        p_count = int(p_count_raw) if (p_count_raw and p_count_raw.isdigit()) else 1
                        
                        cursor.execute(sql, (
                            admin_user, event_key, 
                            current_row_data.get("region", ""),
                            current_row_data.get("training_level", ""),
                            current_row_data.get("company_name", ""),
                            current_row_data.get("contract_period", ""),
                            p_count, name,
                            current_row_data.get("training_level", ""), # 暫代職稱
                            phone, email,
                            current_row_data.get("contact_person", ""),
                            current_row_data.get("contact_email", ""),
                            current_row_data.get("note", ""),
                            current_row_data.get("seating_chart", ""),
                            "未報到", 
                            current_row_data.get("meal_choice", "未選擇")
                        ))
                        success_count += 1
                
                # 自動註冊活動清單
                cursor.execute("SELECT allowed_events FROM admins WHERE username = %s", (admin_user,))
                row_data = cursor.fetchone()
                allowed_sheets = [s.strip() for s in row_data['allowed_events'].split(',') if s.strip()] if row_data else ["活動報到名單"]
                if event_key not in allowed_sheets:
                    allowed_sheets.append(event_key)
                    cursor.execute("UPDATE admins SET allowed_events = %s, current_event = %s WHERE username = %s", (",".join(allowed_sheets), event_key, admin_user))
                else:
                    cursor.execute("UPDATE admins SET current_event = %s WHERE username = %s", (event_key, admin_user))
                
                session['allowed_sheets'] = allowed_sheets
                session['current_admin_sheet'] = event_key

            conn.commit()
            return jsonify({"success": True, "message": f"🤖 智慧對齊完畢！已成功為管理員「{admin_user}」匯入「{event_key}」共 {success_count} 筆旅客名單！"})
        except Exception as e:
            conn.rollback()
            print(f"❌ [CSV寫入資料庫失敗]: {e}")
            return jsonify({"success": False, "message": f"資料庫寫入失敗: {e}"}), 500
        finally: 
            conn.close()
    except Exception as server_error: 
        return jsonify({"success": False, "message": f"解析核心異常: {server_error}"}), 500

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

@app.route('/api/registrations/add', methods=['POST'])
def add_registration():
    if not session.get('admin_logged_in'): return jsonify({"success": False, "message": "未授權"}), 403
    admin_user, event_key = get_admin_and_event_context()
    data = request.json
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sql = """INSERT INTO event_registrations (admin_user, event_key, name, phone, company_name, seating_chart, status, checkin_time, meal_choice, note) 
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            cursor.execute(sql, (admin_user, event_key, data.get('name'), data.get('phone'), data.get('company'), data.get('seat', '現場安排'), '已報到', datetime.now(), data.get('meal', '未選擇'), '現場臨時報到'))
        conn.commit()
        return jsonify({"success": True})
    finally: conn.close()

@app.route('/api/user/info')
def get_user_info():
    if not session.get('admin_logged_in'):
        return jsonify({"success": False, "message": "未登入"}), 401
    return jsonify({
        "success": True, 
        "username": session.get('username', '管理員')
    })

@app.route('/api/current_sheet', methods=['GET'])
def get_current_sheet():
    admin_user = request.args.get('admin')
    if not admin_user:
        return jsonify({"success": False, "error": "Missing admin parameter"}), 400
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_event FROM admins WHERE username = %s", (admin_user,))
            data = cursor.fetchone()
        conn.close()
        
        if data:
            return jsonify({"success": True, "current_sheet": data['current_event']})
        return jsonify({"success": False, "error": "User not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/admin')
def admin_page():
    if not session.get('admin_logged_in'): return send_from_directory('.', 'login.html')
    return send_from_directory('.', 'admin.html')

@app.route('/')
def index(): return send_from_directory('.', '活動報到系統.html')

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
        logs = [{"name": r['name'], "time": r['checkin_time'].strftime('%H:%M:%S') if r['checkin_time'] else "", "company": r['company_name'], "meal": r['meal_choice']} for r in checked[:25]]
        return jsonify({"success": True, "stats": {"total": total, "checked_in": len(checked), "not_checked_in": total-len(checked), "logs": logs, "table_stats": {}}})
    finally: conn.close()

@app.route('/api/search/<method>')
def search(method):
    admin_user, event_key = get_admin_and_event_context()
    q = request.args.get(method, "").strip()
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if method == 'phone':
                cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s AND (phone LIKE %s OR name LIKE %s)", (admin_user, event_key, f"%{q}%", f"%{q}%"))
            else:
                cursor.execute("SELECT * FROM event_registrations WHERE admin_user = %s AND event_key = %s AND name LIKE %s", (admin_user, event_key, f"%{q}%"))
            res = cursor.fetchall()
            for r in res:
                r['id'] = str(r['id']); r['company'] = r['company_name']; r['meal'] = r['meal_choice']; r['seat'] = r['seating_chart']
            return jsonify({"success": True, "data": res})
    finally: conn.close()

@app.route('/api/checkin/<pid>', methods=['POST'])
def checkin(pid):
    admin_user, event_key = get_admin_and_event_context()
    data = request.json
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            status_val = 'checked_in' if data.get('is_original', True) else '替代'
            cursor.execute("UPDATE event_registrations SET checkin_time = %s, status = %s, meal_choice = %s WHERE id = %s AND admin_user = %s", (datetime.now(), status_val, data.get('meal', '未選擇'), pid, admin_user))
        conn.commit()
        return jsonify({"success": True})
    finally: conn.close()

def auto_init_mysql_tables():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM admins")
            if cursor.fetchone()['cnt'] == 0:
                cursor.execute("INSERT INTO admins (username, password, allowed_events) VALUES (%s, %s, %s)", ("admin", "admin123", "活動報到名單"))
                conn.commit()
                print("💡 [MySQL 初始化] 已成功建立預設 admin 帳號")
    except Exception as e: print(f"❌ [MySQL 初始化失敗]: {e}")
    finally: conn.close()

if __name__ == '__main__':
    auto_init_mysql_tables()
    app.run(host='0.0.0.0', port=10000)
