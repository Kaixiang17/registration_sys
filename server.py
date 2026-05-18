import os, json, time, threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = "rcsa_ark_secure_key_20260508" 
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
RENDER_KEY = "/etc/secrets/google-creds.json"
LOCAL_KEY = os.path.join(BASE_DIR, 'test0417-493608-ec0a369af886.json')

participants_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
CACHE_TTL = 300

def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_path = RENDER_KEY if os.path.exists(RENDER_KEY) else LOCAL_KEY
    if not os.path.exists(json_path): json_path = os.path.join(BASE_DIR, 'test0417-493608-ec0a369af886.json')
    return gspread.authorize(Credentials.from_service_account_file(json_path, scopes=scope))

def get_worksheet(name=None):
    # 這裡固定從本地讀取基本試算表名稱，避免雞生蛋蛋生雞的循環
    sheet_name = "活動報到名單"
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                sheet_name = json.load(f).get('google_sheet_name', '活動報到名單')
        except: pass
    spreadsheet = get_gspread_client().open(sheet_name)
    if name:
        try: return spreadsheet.worksheet(name)
        except: return None
    return spreadsheet.get_worksheet(0)

# ==================== 【核心升級：從 Google Sheet 永久載入設定】 ====================
def load_config_from_sheet():
    base_config = {"google_sheet_name": "活動報到名單", "map_image_url": "", "products": [], "excel_columns": {"id": 6, "name": 6, "phone": 8, "company": 3, "status": 15, "meal": 16}}
    # 先融合本地基本配置
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f: base_config.update(json.load(f))
        except: pass
        
    try:
        client = get_gspread_client()
        spreadsheet = client.open(base_config['google_sheet_name'])
        
        # 1. 讀取「系統設定」分頁（若不存在則全自動建立）
        try:
            ws_sys = spreadsheet.worksheet("系統設定")
            sys_values = ws_sys.get_all_records()
            if sys_values:
                base_config["map_image_url"] = sys_values[0].get("map_image_url", "")
                base_config["google_sheet_name"] = sys_values[0].get("google_sheet_name", base_config['google_sheet_name'])
        except:
            ws_sys = spreadsheet.add_worksheet(title="系統設定", rows="2", cols="2")
            ws_sys.append_row(["google_sheet_name", "map_image_url"])
            ws_sys.append_row([base_config["google_sheet_name"], ""])

        # 2. 讀取「商品清單」分頁（若不存在則全自動建立）
        try:
            ws_prod = spreadsheet.worksheet("商品清單")
            prod_rows = ws_prod.get_all_records()
            products = []
            for r in prod_rows:
                products.append({
                    "name": str(r.get("name", "")),
                    "image": str(r.get("image", "")),
                    "category": str(r.get("category", "")),
                    "description": str(r.get("description", "")),
                    "link": str(r.get("link", "")),
                    "isGift": str(r.get("isGift", "")).lower() == "true"
                })
            base_config["products"] = products
        except:
            ws_prod = spreadsheet.add_worksheet(title="商品清單", rows="100", cols="6")
            ws_prod.append_row(["name", "image", "category", "description", "link", "isGift"])

    except Exception as e:
        print(f"⚠️ 雲端配置同步失敗，改從本地暫存讀取: {e}")
    return base_config

# ==================== 【核心升級：將設定永久儲存至 Google Sheet】 ====================
def save_config_to_sheet(config_data):
    # 本地覆寫做為緊急快取
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=4)
    except: pass

    try:
        client = get_gspread_client()
        spreadsheet = client.open(config_data.get('google_sheet_name', '活動報到名單'))
        
        # 寫入「系統設定」分頁
        try: ws_sys = spreadsheet.worksheet("系統設定")
        except:
            ws_sys = spreadsheet.add_worksheet(title="系統設定", rows="2", cols="2")
            ws_sys.append_row(["google_sheet_name", "map_image_url"])
        ws_sys.update('A2:B2', [[config_data.get("google_sheet_name", ""), config_data.get("map_image_url", "")]])

        # 寫入「商品清單」分頁
        try: 
            ws_prod = spreadsheet.worksheet("商品清單")
            ws_prod.clear()
        except:
            ws_prod = spreadsheet.add_worksheet(title="商品清單", rows="100", cols="6")
            
        headers = ["name", "image", "category", "description", "link", "isGift"]
        rows_to_write = [headers]
        for p in config_data.get("products", []):
            rows_to_write.append([
                p.get("name", ""),
                p.get("image", ""),
                p.get("category", ""),
                p.get("description", ""),
                p.get("link", ""),
                "true" if p.get("isGift") else "false"
            ])
        ws_prod.update('A1', rows_to_write)
        return True
    except Exception as e:
        print(f"❌ 寫入雲端失敗: {e}")
        return False

def async_update_sheet(updates):
    try: get_worksheet().batch_update(updates)
    except Exception as e: print(f"背景同步失敗: {e}")

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

# 改裝完成：核心地圖與商品路由全面對接 Google Sheet 試算表雲端！
@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    if not session.get('admin_logged_in'): return jsonify({"success": False}), 403
    if request.method == 'POST':
        if save_config_to_sheet(request.json):
            return jsonify({"success": True, "data": request.json})
            
        return jsonify({"success": False, "message": "雲端儲存失敗"}), 500
    return jsonify(load_config_from_sheet())

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
    
    # 與雲端載入欄位索引同步
    config_tmp = load_config_from_sheet()
    cols = config_tmp.get('excel_columns', {"id": 6, "name": 6, "phone": 8, "company": 3, "status": 15, "meal": 16})
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

def refresh_cache(force=False):
    global participants_cache, last_cache_update
    if not force and (time.time() - last_cache_update < CACHE_TTL) and participants_cache: return
    with cache_lock:
        try:
            all_values = get_worksheet().get_all_values()
            config_tmp = load_config_from_sheet()
            cols = config_tmp.get('excel_columns', {"id": 6, "name": 6, "phone": 8, "company": 3, "status": 15, "meal": 16})
            new_cache = []
            last_company = ""
            
            for i, row in enumerate(all_values[3:]):
                def g(c): return row[c-1].strip() if c and c-1 < len(row) else ""
                current_comp = g(cols.get('company', 3))
                if current_comp: last_company = current_comp
                name = g(cols.get('name', 6))
                if not name: continue
                
                new_cache.append({
                    "id": f"{name}_{i}", 
                    "name": name, 
                    "phone": g(cols.get('phone', 8)),
                    "company": last_company,
                    "email": g(cols.get('email', 9)),
                    "status": g(cols.get('status', 15)), 
                    "meal": g(cols.get('meal', 16)),
                    "checkedInAt": g(cols.get('checkedInAt', 14)), 
                    "seat": g(cols.get('seat', 13)), 
                    "table": g(cols.get("seat", 13))[:2] if g(cols.get("seat", 13))[:2].isdigit() else "", 
                    "_row": i + 4 
                })
            participants_cache = new_cache
            last_cache_update = time.time()
        except Exception as e: print(f"緩存刷新失敗: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
