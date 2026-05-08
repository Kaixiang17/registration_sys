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

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f: return json.load(f)
        except: return {}
    return {"google_sheet_name": "活動報到名單", "excel_columns": {}}

def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_path = RENDER_KEY if os.path.exists(RENDER_KEY) else LOCAL_KEY
    if not os.path.exists(json_path): json_path = os.path.join(BASE_DIR, 'test0417-493608-ec0a369af886.json')
    return gspread.authorize(Credentials.from_service_account_file(json_path, scopes=scope))

def get_worksheet(name=None):
    config = load_config()
    sheet_name = config.get('google_sheet_name', '活動報到名單')
    spreadsheet = get_gspread_client().open(sheet_name)
    if name:
        try: return spreadsheet.worksheet(name)
        except: return None
    return spreadsheet.get_worksheet(0)

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
    return jsonify({
        "success": True,
        "stats": { "total": total, "checked_in": len(checked_in_list), "not_checked_in": total - len(checked_in_list), "logs": logs[:25], "table_stats": get_table_stats() }
    })

def get_table_stats():
    stats = {}
    for p in participants_cache:
        t = p.get("table", "").strip()
        if not t: continue
        if t not in stats: stats[t] = {"total": 0, "checked_in": 0}
        stats[t]["total"] += 1
        if p["status"] in ["checked_in", "已報到", "替代"]: stats[t]["checked_in"] += 1
    return {t: {"total": s["total"], "checked_in": s["checked_in"], "rate": round(s["checked_in"]/s["total"]*100, 1)} for t, s in stats.items() if s["total"] > 0}

@app.route('/')
def index(): return send_from_directory('.', '活動報到系統.html')

@app.route('/api/config')
def get_config(): return jsonify(load_config())

# --- 搜尋邏輯優化：解決 [object Object] 與非該公司員工問題 ---
@app.route('/api/search/<method>')
def search(method):
    refresh_cache()
    q = request.args.get(method, "").strip().lower()
    
    if method == 'company':
        # 僅回傳公司名稱清單，避免回傳物件
        matched_companies = sorted(list(set(p.get('company', '') for p in participants_cache if q in p.get('company', '').lower() and p.get('company'))))
        return jsonify({"success": True, "data": matched_companies})
    
    if method == 'company_members':
        # 嚴格過濾：僅回傳完全符合公司名稱的人員
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
    cols = load_config().get('excel_columns', {})
    status_val = 'checked_in' if is_original else '替代'
    
    updates = [
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('checkedInAt', 14))), 'values': [[now_tw]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('status', 15))), 'values': [[status_val]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('meal', 16))), 'values': [[meal]]}
    ]
    
    # Q(17), R(18), S(19) 精準寫入
    p_name_col, p_phone_col, p_email_col = 17, 18, 19
    if not is_original and proxy_info:
        updates.extend([
            {'range': gspread.utils.rowcol_to_a1(p['_row'], p_name_col), 'values': [[proxy_info.get('name', '')]]},
            {'range': gspread.utils.rowcol_to_a1(p['_row'], p_phone_col), 'values': [[proxy_info.get('phone', '')]]},
            {'range': gspread.utils.rowcol_to_a1(p['_row'], p_email_col), 'values': [[proxy_info.get('email', '')]]}
        ])
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
            cols = load_config().get('excel_columns', {})
            new_cache = []
            for i, row in enumerate(all_values[3:]):
                def g(c): return row[c-1].strip() if c and c-1 < len(row) else ""
                name = g(cols.get('name', 6))
                if not name: continue
                new_cache.append({
                    "id": f"{name}_{i}", "name": name, "phone": g(cols.get('phone', 8)),
                    "company": g(cols.get('company', 3)), "email": g(cols.get('email', 9)),
                    "status": g(cols.get('status', 15)), "meal": g(cols.get('meal', 16)),
                    "checkedInAt": g(cols.get('checkedInAt', 14)), "seat": g(cols.get('seat', 13)), 
                    "_row": i + 4 
                })
            participants_cache = new_cache
            last_cache_update = time.time()
        except Exception as e: print(f"同步失敗: {e}")

if __name__ == '__main__':
    refresh_cache(True)
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
