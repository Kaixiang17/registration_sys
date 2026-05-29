import os, json, time, threading, requests, csv, io
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = "rcsa_ark_secure_key_20260508_multitenant" 
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RENDER_KEY = "/etc/secrets/google-creds.json"
LOCAL_KEY = os.path.join(BASE_DIR, 'test0417-493608-ec0a369af886.json')

# ============================================================
# 【多租戶快取隔離】快取改為字典結構，Key為各公司的 sheet_name
# ============================================================
participants_cache = {}
last_cache_update = {}
cache_lock = threading.Lock()
CACHE_TTL = 300

config_cache = {}
last_config_update = {}
CONFIG_TTL = 600

def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_path = RENDER_KEY if os.path.exists(RENDER_KEY) else LOCAL_KEY
    if not os.path.exists(json_path): json_path = os.path.join(BASE_DIR, 'test0417-493608-ec0a369af886.json')
    return gspread.authorize(Credentials.from_service_account_file(json_path, scopes=scope))

def get_current_sheet_name():
    """ 核心樞紐：動態判斷目前請求該讀取哪一個資料庫 """
    # 1. 前端訪客：透過 URL ?sheet= 或 API 內含的 sheet 參數
    guest_sheet = request.args.get('sheet')
    if not guest_sheet and request.is_json and 'sheet' in request.json:
        guest_sheet = request.json['sheet']
    if guest_sheet:
        return guest_sheet
    
    # 2. 後台管理員：讀取他目前在系統設定中選定的專屬資料庫
    if session.get('admin_logged_in') and session.get('current_admin_sheet'):
        return session.get('current_admin_sheet')
        
    # 3. 預設值
    return "活動報到名單"

def get_worksheet(name=None):
    sheet_name = get_current_sheet_name()
    try:
        spreadsheet = get_gspread_client().open(sheet_name)
        if name: return spreadsheet.worksheet(name)
        return spreadsheet.get_worksheet(0)
    except Exception as e:
        print(f"❌ 找不到試算表 [{sheet_name}]: {e}")
        return None

def upload_image_to_free_pool(base64_str):
    if not base64_str or not str(base64_str).startswith("data:image/"): return base64_str
    try:
        base64_data = base64_str.split(",")[1] if "," in base64_str else base64_str
        res = requests.post("https://api.imgbb.com/1/upload", data={"key": "2b3149867c4b69cdbda90ea8fbd52ec3", "image": base64_data}, timeout=15).json()
        if res.get("success"): return res["data"]["url"]
    except Exception as e: print(f"❌ [圖床失敗]: {e}")
    return base64_str

def load_config_from_sheets(force_refresh=False):
    sheet_name = get_current_sheet_name()
    global config_cache, last_config_update
    
    if not force_refresh and sheet_name in config_cache and (time.time() - last_config_update.get(sheet_name, 0) < CONFIG_TTL):
        return config_cache[sheet_name]

    config_data = {
        "show_meal_options": True, "google_sheet_name": sheet_name, "map_image_url": "", "products": [],
        "excel_columns": {"id": 6, "name": 6, "phone": 8, "company": 3, "email": 9, "qrCode": 4, "registeredAt": 5, "checkedInAt": 14, "status": 15, "meal": 16}
    }
    try:
        ws_cfg = get_worksheet("系統設定")
        if ws_cfg:
            vals = ws_cfg.get_all_values()
            if len(vals) > 1:
                row = vals[1]
                config_data["show_meal_options"] = str(row[0]).upper() == "TRUE" if len(row) > 0 else True
                config_data["map_image_url"] = str(row[1]) if len(row) > 1 else ""
                
        ws_prod = get_worksheet("商品清單")
        if ws_prod:
            for r in ws_prod.get_all_records():
                if not r.get("商品名稱"): continue
                config_data["products"].append({
                    "name": str(r.get("商品名稱")), "image": str(r.get("商品圖片", "")), "category": str(r.get("商品分類", "課程")),
                    "description": str(r.get("商品描述", "")), "link": str(r.get("購買連結", "")), "isGift": str(r.get("是否為贈品", "FALSE")).upper() == "TRUE"
                })
        config_cache[sheet_name] = config_data
        last_config_update[sheet_name] = time.time()
    except Exception as e:
        print(f"⚠️ [{sheet_name}] 讀取設定失敗: {e}")
        if sheet_name not in config_cache: config_cache[sheet_name] = config_data
    return config_cache[sheet_name]

def async_save_process(payload, sheet_name):
    print(f"🛰️ [背景同步] 正在寫入 {sheet_name} 的系統設定...")
    try:
        if payload.get("map_image_url"): payload["map_image_url"] = upload_image_to_free_pool(payload["map_image_url"])
        if payload.get("products"):
            for p in payload["products"]:
                if p.get("image"): p["image"] = upload_image_to_free_pool(p["image"])
        
        client = get_gspread_client()
        spreadsheet = client.open(sheet_name)
        
        ws_cfg = spreadsheet.worksheet("系統設定")
        if ws_cfg:
            ws_cfg.clear()
            ws_cfg.update('A1:C2', [["顯示餐點選項", "地圖圖片網址", "Google試算表名稱"],
                                    ["TRUE" if payload.get("show_meal_options", True) else "FALSE", payload.get("map_image_url", ""), sheet_name]])
            
        ws_prod = spreadsheet.worksheet("商品清單")
        if ws_prod and "products" in payload:
            ws_prod.clear()
            rows_to_write = [["商品名稱", "商品圖片", "商品分類", "商品描述", "購買連結", "是否為贈品"]]
            for p in payload["products"]:
                rows_to_write.append([p.get("name", ""), p.get("image", ""), p.get("category", "課程"), p.get("description", ""), p.get("link", ""), "TRUE" if p.get("isGift") else "FALSE"])
            ws_prod.update(f'A1:F{len(rows_to_write)}', rows_to_write)
        
        config_cache[sheet_name] = payload
        last_config_update[sheet_name] = time.time()
    except Exception as e: print(f"❌ [背景同步失敗]: {e}")

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    sheet_name = get_current_sheet_name()
    if request.method == 'POST':
        if not session.get('admin_logged_in'): return jsonify({"success": False, "message": "未授權的操作"}), 403
        payload = request.json
        
        # 【資料庫切換邏輯】如果管理員從下拉選單切換了資料庫，在此更新 Session
        new_sheet = payload.get("google_sheet_name")
        if new_sheet and new_sheet != session.get('current_admin_sheet'):
            if new_sheet in session.get('allowed_sheets', []):
                session['current_admin_sheet'] = new_sheet
                sheet_name = new_sheet
        
        current_data = load_config_from_sheets(force_refresh=True)
        if "products" not in payload or not payload["products"]: payload["products"] = current_data.get("products", [])
        if "map_image_url" not in payload: payload["map_image_url"] = current_data.get("map_image_url", "")
        
        threading.Thread(target=async_save_process, args=(payload, sheet_name)).start()
        return jsonify({"success": True, "message": "儲存成功！系統已同步至該資料庫。"})
    return jsonify(load_config_from_sheets())

@app.route('/api/sheets/list', methods=['GET'])
def list_available_sheets():
    if not session.get('admin_logged_in'): return jsonify({"success": False, "message": "未授權的操作"}), 403
    # 【核心安全限制】不再使用 openall()，只回傳該管理員被授權的資料庫清單
    allowed = session.get('allowed_sheets', ["活動報到名單"])
    return jsonify({"success": True, "sheets": allowed})

@app.route('/api/sheets/upload_csv', methods=['POST'])
def upload_csv_to_sheet():
    if not session.get('admin_logged_in'): return jsonify({"success": False, "message": "未授權的操作"}), 403
    if 'file' not in request.files: return jsonify({"success": False, "message": "找不到檔案"}), 400
    file = request.files['file']
    try:
        stream = io.StringIO(file.stream.read().decode("UTF-8"), newline=None)
        rows_to_write = list(csv.reader(stream))
        if not rows_to_write: return jsonify({"success": False, "message": "檔案為空"}), 400

        ws = get_worksheet() # 自動寫入他目前選中的那家公司的資料庫
        ws.clear()
        ws.update(f'A1:Z{len(rows_to_write)}', rows_to_write)
        refresh_cache(force=True)
        return jsonify({"success": True, "message": "名單 CSV 上傳覆寫成功！"})
    except Exception as e: return jsonify({"success": False, "message": f"寫入失敗: {e}"}), 500

@app.route('/api/login', methods=['POST'])
def admin_login():
    data = request.json
    u, p = data.get('username'), data.get('password')
    try:
        # 永遠回到「活動報到名單」這張主表的「管理員」分頁進行身分核對
        client = get_gspread_client()
        ws = client.open("活動報到名單").worksheet("管理員")
        for row in ws.get_all_records():
            if str(row.get('帳號', '')) == str(u) and str(row.get('密碼', '')) == str(p):
                session['admin_logged_in'] = True
                # 讀取該管理員負責的資料庫 (支援逗號分隔多個表)
                allowed_raw = str(row.get('授權試算表', '活動報到名單'))
                allowed_sheets = [s.strip() for s in allowed_raw.split(',') if s.strip()]
                if not allowed_sheets: allowed_sheets = ["活動報到名單"]
                
                session['allowed_sheets'] = allowed_sheets
                session['current_admin_sheet'] = allowed_sheets[0] # 預設切換到第一個授權庫
                return jsonify({"success": True})
        return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401
    except Exception as e: return jsonify({"success": False, "message": f"登入異常: {e}"}), 500

@app.route('/api/logout')
def logout():
    session.clear()
    return redirect('/login.html')

@app.route('/admin')
def admin_page():
    if not session.get('admin_logged_in'): return send_from_directory('.', 'login.html')
    return send_from_directory('.', 'admin.html')

@app.route('/')
def index(): return send_from_directory('.', '活動報到系統.html')

@app.route('/api/dashboard_stats')
def get_dashboard_stats():
    if not session.get('admin_logged_in'): return jsonify({"success": False}), 403
    sheet_name = get_current_sheet_name()
    refresh_cache()
    
    current_cache = participants_cache.get(sheet_name, [])
    total = len(current_cache)
    checked_in_list = [p for p in current_cache if p['status'] in ['checked_in', '已報到', '替代']]
    logs = [{"name": f"{p['name']} (替代)" if p['status'] == '替代' else p['name'], "time": p['checkedInAt'], "company": p['company'], "meal": p['meal']} for p in checked_in_list]
    logs.sort(key=lambda x: x['time'], reverse=True)
    
    stats_data = {}
    for p in current_cache:
        t = p.get("table", "").strip()
        if not t: continue
        if t not in stats_data: stats_data[t] = {"total": 0, "checked": 0}
        stats_data[t]["total"] += 1
        if p["status"] in ["checked_in", "已報到", "替代"]: stats_data[t]["checked"] += 1
    
    table_stats = {t: round(s["checked"]/s["total"]*100, 1) for t, s in stats_data.items() if s["total"] > 0}
    return jsonify({"success": True, "stats": { "total": total, "checked_in": len(checked_in_list), "not_checked_in": total - len(checked_in_list), "logs": logs[:25], "table_stats": table_stats }})

@app.route('/api/search/<method>')
def search(method):
    sheet_name = get_current_sheet_name()
    refresh_cache()
    q = request.args.get(method, "").strip().lower()
    current_cache = participants_cache.get(sheet_name, [])
    
    if method == 'company':
        return jsonify({"success": True, "data": sorted(list(set(p.get('company', '') for p in current_cache if q in p.get('company', '').lower() and p.get('company'))))})
    if method == 'company_members':
        company_name = request.args.get('name', '').strip().lower()
        return jsonify({"success": True, "data": [p for p in current_cache if p.get('company', '').lower() == company_name]})
    return jsonify({"success": True, "data": [p for p in current_cache if q in p.get(method, "").lower() or q in p.get('name', '').lower()]})

@app.route('/api/checkin/<pid>', methods=['POST'])
def checkin(pid):
    sheet_name = get_current_sheet_name()
    data = request.json
    now_tw = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M:%S')
    current_cache = participants_cache.get(sheet_name, [])
    
    p = next((x for x in current_cache if x['id'] == pid), None)
    if not p: return jsonify({"success": False}), 404
    if p['status'] in ['checked_in', '已報到', '替代']: return jsonify({"success": False, "error": "already_done", "data": p})
    
    meal, is_original, proxy_info = data.get('meal', '未選擇'), data.get('is_original', True), data.get('proxy_info', {})
    cols = load_config_from_sheets().get('excel_columns', {})
    status_val = 'checked_in' if is_original else '替代'
    
    updates = [
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('checkedInAt', 14))), 'values': [[now_tw]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('status', 15))), 'values': [[status_val]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('meal', 16))), 'values': [[meal]]}
    ]
    p_name_col, p_phone_col, p_email_col = 17, 18, 19
    if not is_original and proxy_info:
        updates.extend([
            {'range': gspread.utils.rowcol_to_a1(p['_row'], p_name_col), 'values': [[proxy_info.get('name', '')]]},
            {'range': gspread.utils.rowcol_to_a1(p['_row'], p_phone_col), 'values': [[proxy_info.get('phone', '')]]},
            {'range': gspread.utils.rowcol_to_a1(p['_row'], p_email_col), 'values': [[proxy_info.get('email', '')]]}
        ])
    else:
        updates.extend([{'range': gspread.utils.rowcol_to_a1(p['_row'], c), 'values': [['']]} for c in [p_name_col, p_phone_col, p_email_col]])
            
    threading.Thread(target=async_update_sheet, args=(updates, sheet_name)).start()
    p.update({"status": status_val, "meal": meal, "checkedInAt": now_tw})
    return jsonify({"success": True, "data": p})

def async_update_sheet(updates, sheet_name):
    try: 
        client = get_gspread_client()
        client.open(sheet_name).get_worksheet(0).batch_update(updates)
    except Exception as e: print(f"背景同步失敗: {e}")

def refresh_cache(force=False):
    global participants_cache, last_cache_update
    sheet_name = get_current_sheet_name()
    if not force and sheet_name in participants_cache and (time.time() - last_cache_update.get(sheet_name, 0) < CACHE_TTL) and participants_cache[sheet_name]: return
    with cache_lock:
        try:
            ws = get_worksheet()
            if not ws: return
            all_values = ws.get_all_values()
            cols = load_config_from_sheets().get('excel_columns', {})
            new_cache = []
            last_company = ""
            for i, row in enumerate(all_values[3:]):
                def g(c): return row[c-1].strip() if c and c-1 < len(row) else ""
                if g(cols.get('company', 3)): last_company = g(cols.get('company', 3))
                name = g(cols.get('name', 6))
                if not name: continue
                new_cache.append({
                    "id": f"{name}_{i}", "name": name, "phone": g(cols.get('phone', 8)), "company": last_company,
                    "email": g(cols.get('email', 9)), "status": g(cols.get('status', 15)), "meal": g(cols.get('meal', 16)),
                    "checkedInAt": g(cols.get('checkedInAt', 14)), "seat": g(cols.get('seat', 13)), 
                    "table": g(cols.get("seat", 13))[:2] if g(cols.get("seat", 13))[:2].isdigit() else "", "_row": i + 4 
                })
            participants_cache[sheet_name] = new_cache
            last_cache_update[sheet_name] = time.time()
        except Exception as e: print(f"緩存刷新失敗: {e}")

def auto_check_and_patch_sheets():
    try:
        client = get_gspread_client()
        spreadsheet = client.open("活動報到名單")
        try: spreadsheet.worksheet("管理員")
        except:
            ws = spreadsheet.add_worksheet(title="管理員", rows="10", cols="5")
            # 初始化時自動建立「授權試算表」這個重要欄位
            ws.update('A1:C2', [["帳號", "密碼", "授權試算表"], ["admin", "admin123", "活動報到名單"]])
        try: spreadsheet.worksheet("系統設定")
        except:
            ws = spreadsheet.add_worksheet(title="系統設定", rows="10", cols="5")
            ws.update('A1:C2', [["顯示餐點選項", "地圖圖片網址", "Google試算表名稱"], ["TRUE", "", "活動報到名單"]])
        try: spreadsheet.worksheet("商品清單")
        except:
            ws = spreadsheet.add_worksheet(title="商品清單", rows="50", cols="10")
            ws.append_row(["商品名稱", "商品圖片", "商品分類", "商品描述", "購買連結", "是否為贈品"])
    except Exception as e: print(f"❌ [初始化失敗]: {e}")

auto_check_and_patch_sheets()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
