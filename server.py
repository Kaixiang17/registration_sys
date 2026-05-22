import os, json, time, threading, requests, csv, io
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = "rcsa_ark_secure_key_20260508" 
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RENDER_KEY = "/etc/secrets/google-creds.json"
LOCAL_KEY = os.path.join(BASE_DIR, 'test0417-493608-ec0a369af886.json')

participants_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
CACHE_TTL = 300

config_cache = None
last_config_update = 0
CONFIG_TTL = 600

def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_path = RENDER_KEY if os.path.exists(RENDER_KEY) else LOCAL_KEY
    if not os.path.exists(json_path): json_path = os.path.join(BASE_DIR, 'test0417-493608-ec0a369af886.json')
    return gspread.authorize(Credentials.from_service_account_file(json_path, scopes=scope))

def get_worksheet(name=None):
    # 核心安全修正：從記憶體快取動態取得目標試算表名稱，避免讀取初始化時產生無窮循環遞迴
    sheet_name = "活動報到名單"
    if config_cache and "google_sheet_name" in config_cache:
        sheet_name = config_cache["google_sheet_name"]
        
    spreadsheet = get_gspread_client().open(sheet_name)
    if name:
        try: return spreadsheet.worksheet(name)
        except: return None
    return spreadsheet.get_worksheet(0)

def upload_image_to_free_pool(base64_str):
    if not base64_str or not str(base64_str).startswith("data:image/"):
        return base64_str
    try:
        if "," in base64_str:
            base64_data = base64_str.split(",")[1]
        else:
            base64_data = base64_str
        url = "https://api.imgbb.com/1/upload"
        payload = {
            "key": "2b3149867c4b69cdbda90ea8fbd52ec3",
            "image": base64_data
        }
        res = requests.post(url, data=payload, timeout=15).json()
        if res.get("success"):
            return res["data"]["url"]
    except Exception as e:
        print(f"❌ [圖床失敗]: {e}")
    return base64_str

def load_config_from_sheets(force_refresh=False):
    global config_cache, last_config_update
    if not force_refresh and config_cache and (time.time() - last_config_update < CONFIG_TTL):
        return config_cache

    config_data = {
        "show_meal_options": True, "google_sheet_name": "活動報到名單", "map_image_url": "", "products": [],
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
                config_data["google_sheet_name"] = str(row[2]) if len(row) > 2 else "活動報到名單"
                
        ws_prod = get_worksheet("商品清單")
        if ws_prod:
            prod_rows = ws_prod.get_all_records()
            for r in prod_rows:
                if not r.get("商品名稱"): continue
                config_data["products"].append({
                    "name": str(r.get("商品名稱")),
                    "image": str(r.get("商品圖片", "")),
                    "category": str(r.get("商品分類", "課程")),
                    "description": str(r.get("商品描述", "")),
                    "link": str(r.get("購買連結", "")),
                    "isGift": str(r.get("是否為贈品", "FALSE")).upper() == "TRUE"
                })
        config_cache = config_data
        last_config_update = time.time()
    except Exception as e:
        print(f"⚠️ [讀取失敗] 沿用舊快取: {e}")
        if not config_cache: config_cache = config_data
    return config_cache

def async_save_process(payload, current_data):
    """ 背景處理：強制在 Google Sheet 建立指定格子存放地圖，絕對不卡死 """
    print("🛰️ [背景同步] 正在將地圖與商品寫入指定 Google Sheet 格子...")
    try:
        if payload.get("map_image_url"):
            payload["map_image_url"] = upload_image_to_free_pool(payload["map_image_url"])
        if payload.get("products"):
            for p in payload["products"]:
                if p.get("image"):
                    p["image"] = upload_image_to_free_pool(p["image"])
        
        ws_cfg = get_worksheet("系統設定")
        if ws_cfg:
            ws_cfg.clear()
            ws_cfg.update('A1:C2', [
                ["顯示餐點選項", "地圖圖片網址", "Google試算表名稱"],
                [
                    "TRUE" if payload.get("show_meal_options", True) else "FALSE",
                    payload.get("map_image_url", ""),
                    payload.get("google_sheet_name", "活動報到名單")
                ]
            ])
            
        ws_prod = get_worksheet("商品清單")
        if ws_prod and "products" in payload:
            ws_prod.clear()
            rows_to_write = [["商品名稱", "商品圖片", "商品分類", "商品描述", "購買連結", "是否為贈品"]]
            for p in payload["products"]:
                rows_to_write.append([
                    p.get("name", ""), p.get("image", ""), p.get("category", "課程"),
                    p.get("description", ""), p.get("link", ""), "TRUE" if p.get("isGift") else "FALSE"
                ])
            ws_prod.update(f'A1:F{len(rows_to_write)}', rows_to_write)
        
        global config_cache, last_config_update
        config_cache = payload
        last_config_update = time.time()
        print("🟢 [背景同步] 雲端試算表格子已完全對齊，系統設定儲存成功！")
    except Exception as e:
        print(f"❌ [背景同步失敗]: {e}")

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    if request.method == 'POST':
        if not session.get('admin_logged_in'): 
            return jsonify({"success": False, "message": "未授權的操作"}), 403
            
        payload = request.json
        current_data = load_config_from_sheets()
        
        if "products" not in payload or not payload["products"]:
            payload["products"] = current_data.get("products", [])
            
        if "map_image_url" not in payload:
            payload["map_image_url"] = current_data.get("map_image_url", "")

        threading.Thread(target=async_save_process, args=(payload, current_data)).start()
        return jsonify({"success": True, "message": "儲存成功！系統正在背景寫入 Google Sheet。"})
            
    return jsonify(load_config_from_sheets())

# ============================================================
# 【全新優化 1】自動撈取目前 API 金鑰帳戶底下的所有 Google 試算表
# ============================================================
@app.route('/api/sheets/list', methods=['GET'])
def list_available_sheets():
    if not session.get('admin_logged_in'): 
        return jsonify({"success": False, "message": "未授權的操作"}), 403
    try:
        client = get_gspread_client()
        all_sheets = client.openall()
        sheets_list = [s.title for s in all_sheets]
        return jsonify({"success": True, "sheets": sheets_list})
    except Exception as e:
        return jsonify({"success": False, "message": f"無法自動取得帳戶內試算表清單: {e}"}), 500

# ============================================================
# 【全新優化 2】接收本機 CSV 檔案，一鍵清洗並強制更新目前的試算表名單
# ============================================================
@app.route('/api/sheets/upload_csv', methods=['POST'])
def upload_csv_to_sheet():
    if not session.get('admin_logged_in'): 
        return jsonify({"success": False, "message": "未授權的操作"}), 403
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "找不到上傳的檔案"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "未選擇檔案"}), 400
    try:
        stream = io.StringIO(file.stream.read().decode("UTF-8"), newline=None)
        csv_input = csv.reader(stream)
        rows_to_write = list(csv_input)
        if not rows_to_write:
            return jsonify({"success": False, "message": "檔案內容不能為空"}), 400

        ws = get_worksheet()
        ws.clear()
        ws.update(f'A1:Z{len(rows_to_write)}', rows_to_write)
        
        refresh_cache(force=True)
        return jsonify({"success": True, "message": "名單 CSV 上傳覆寫成功，已完美同步至雲端！"})
    except Exception as e:
        return jsonify({"success": False, "message": f"洗入 Google Sheet 失敗: {e}"}), 500

@app.route('/api/login', methods=['POST'])
def admin_login():
    data = request.json
    u, p = data.get('username'), data.get('password')
    ws = get_worksheet("管理員")
    if not ws: return jsonify({"success": False, "message": "尚未建立管理員分頁"}), 500
    try:
        sheet_username = ws.cell(2, 1).value
        sheet_password = ws.cell(2, 3).value
        if str(u) == str(sheet_username) and str(p) == str(sheet_password):
            session['admin_logged_in'] = True
            return jsonify({"success": True})
    except Exception as e: return jsonify({"success": False, "message": f"讀取失敗: {e}"}), 500
    return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

@app.route('/api/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect('/login.html')

@app.route('/admin')
def admin_page():
    if not session.get('admin_logged_in'): return send_from_directory('.', 'login.html')
    return send_from_directory('.', 'admin.html')

@app.route('/api/dashboard_stats')
def get_dashboard_stats():
    if not session.get('admin_logged_in'): return jsonify({"success": False}), 403
    refresh_cache()
    total = len(participants_cache)
    checked_in_list = [p for p in participants_cache if p['status'] in ['checked_in', '已報到', '替代']]
    logs = [{"name": f"{p['name']} (替代)" if p['status'] == '替代' else p['name'], "time": p['checkedInAt'], "company": p['company'], "meal": p['meal']} for p in checked_in_list]
    logs.sort(key=lambda x: x['time'], reverse=True)
    
    stats_data = {}
    for p in participants_cache:
        t = p.get("table", "").strip()
        if not t: continue
        if t not in stats_data: stats_data[t] = {"total": 0, "checked": 0}
        stats_data[t]["total"] += 1
        if p["status"] in ["checked_in", "已報到", "替代"]: stats_data[t]["checked"] += 1
    
    table_stats = {t: round(s["checked"]/s["total"]*100, 1) for t, s in stats_data.items() if s["total"] > 0}

    return jsonify({
        "success": True,
        "stats": { "total": total, "checked_in": len(checked_in_list), "not_checked_in": total - len(checked_in_list), "logs": logs[:25], "table_stats": table_stats }
    })

@app.route('/')
def index(): return send_from_directory('.', '活動報到系統.html')

@app.route('/api/search/<method>')
def search(method):
    refresh_cache()
    q = request.args.get(method, "").strip().lower()
    if method == 'company':
        matched_companies = sorted(list(set(p.get('company', '') for p in participants_cache if q in p.get('company', '').lower() and p.get('company'))))
        return jsonify({"success": True, "data": matched_companies})
    if method == 'company_members':
        company_name = request.args.get('name', '').strip().lower()
        members = [p for p in participants_cache if p.get('company', '').lower() == company_name]
        return jsonify({"success": True, "data": members})
    return jsonify({"success": True, "data": [p for p in participants_cache if q in p.get(method, "").lower() or q in p.get('name', '').lower()]})

@app.route('/api/checkin/<pid>', methods=['POST'])
def checkin(pid):
    data = request.json
    now_tw = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M:%S')
    p = next((x for x in participants_cache if x['id'] == pid), None)
    if not p: return jsonify({"success": False}), 404
    if p['status'] in ['checked_in', '已報到', '替代']:
        return jsonify({"success": False, "error": "already_done", "data": p})
    
    meal = data.get('meal', '未選擇')
    is_original = data.get('is_original', True)
    proxy_info = data.get('proxy_info', {})
    
    config_tmp = load_config_from_sheets()
    cols = config_tmp.get('excel_columns', {})
    status_val = 'checked_in' if is_original else '替代'
    
    updates = [
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('checkedInAt', 14))), 'values': [[now_tw]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('status', 15))), 'values': [[status_val]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('meal', 16))), 'values': [[meal]]}
    ]
    p_name_col, p_phone_col, p_email_col = 17, 18, 19
    if not is_original and proxy_info:
        updates.append({'range': gspread.utils.rowcol_to_a1(p['_row'], p_name_col), 'values': [[proxy_info.get('name', '')]]})
        updates.append({'range': gspread.utils.rowcol_to_a1(p['_row'], p_phone_col), 'values': [[proxy_info.get('phone', '')]]})
        updates.append({'range': gspread.utils.rowcol_to_a1(p['_row'], p_email_col), 'values': [[proxy_info.get('email', '')]]})
    else:
        updates.extend([{'range': gspread.utils.rowcol_to_a1(p['_row'], c), 'values': [['']]} for c in [p_name_col, p_phone_col, p_email_col]])
            
    threading.Thread(target=async_update_sheet, args=(updates,)).start()
    p.update({"status": status_val, "meal": meal, "checkedInAt": now_tw})
    return jsonify({"success": True, "data": p})

def async_update_sheet(updates):
    try: get_worksheet().batch_update(updates)
    except Exception as e: print(f"背景同步失敗: {e}")

def refresh_cache(force=False):
    global participants_cache, last_cache_update
    if not force and (time.time() - last_cache_update < CACHE_TTL) and participants_cache: return
    with cache_lock:
        try:
            all_values = get_worksheet().get_all_values()
            config_tmp = load_config_from_sheets()
            cols = config_tmp.get('excel_columns', {})
            new_cache = []
            last_company = ""
            for i, row in enumerate(all_values[3:]):
                def g(c): return row[c-1].strip() if c and c-1 < len(row) else ""
                current_comp = g(cols.get('company', 3))
                if current_comp: last_company = current_comp
                name = g(cols.get('name', 6))
                if not name: continue
                new_cache.append({
                    "id": f"{name}_{i}", "name": name, "phone": g(cols.get('phone', 8)), "company": last_company,
                    "email": g(cols.get('email', 9)), "status": g(cols.get('status', 15)), "meal": g(cols.get('meal', 16)),
                    "checkedInAt": g(cols.get('checkedInAt', 14)), "seat": g(cols.get('seat', 13)), 
                    "table": g(cols.get("seat", 13))[:2] if g(cols.get("seat", 13))[:2].isdigit() else "", "_row": i + 4 
                })
            participants_cache = new_cache
            last_cache_update = time.time()
        except Exception as e: print(f"緩存刷新失敗: {e}")

def auto_check_and_patch_sheets():
    try:
        client = get_gspread_client()
        spreadsheet = client.open("活動報到名單")
        try: spreadsheet.worksheet("系統設定")
        except:
            ws = spreadsheet.add_worksheet(title="系統設定", rows="10", cols="5")
            ws.update('A1:C2', [["顯示餐點選項", "地圖圖片網址", "Google試算表名稱"], ["TRUE", "", "活動報到名單"]])
        try: spreadsheet.worksheet("商品清單")
        except:
            ws = spreadsheet.add_worksheet(title="商品清單", rows="50", cols="10")
            ws.append_row(["商品名稱", "商品圖片", "商品分類", "商品描述", "購買連結", "是否為贈品"])
    except Exception as e:
        print(f"❌ [初始化失敗]: {e}")

auto_check_and_patch_sheets()
load_config_from_sheets(force_refresh=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
