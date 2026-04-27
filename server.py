import os
import json
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
RENDER_KEY = "/etc/secrets/google-creds.json"
LOCAL_KEY = os.path.join(BASE_DIR, 'test0417-493608-dce82b8c6901.json')

participants_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
CACHE_TTL = 200

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_path = RENDER_KEY if os.path.exists(RENDER_KEY) else LOCAL_KEY
    return gspread.authorize(Credentials.from_service_account_file(json_path, scopes=scope))

def get_worksheet():
    config = load_config()
    return get_gspread_client().open(config.get('google_sheet_name', '活動報到名單')).get_worksheet(0)

def async_update_sheet(updates):
    try: get_worksheet().batch_update(updates)
    except Exception as e: print(f"背景寫入失敗: {e}")

def refresh_cache(force=False):
    global participants_cache, last_cache_update
    if not force and (time.time() - last_cache_update < CACHE_TTL) and participants_cache:
        return
    with cache_lock:
        try:
            all_values = get_worksheet().get_all_values()
            cols = load_config().get('excel_columns', {})
            new_cache, last_company = [], ""
            for i, row in enumerate(all_values[3:]):
                def g(c): return row[c-1].strip() if c-1 < len(row) else ""
                # 處理合併的公司欄位
                comp = g(cols.get('company', 3))
                if comp: last_company = comp
                name = g(cols.get('name', 6))
                if not name: continue
                new_cache.append({
                    "id": name, "name": name, "phone": g(cols.get('phone', 8)),
                    "company": last_company, "email": g(cols.get('email', 9)),
                    "status": g(cols.get('status', 15)), "meal": g(cols.get('meal', 16)),
                    "_row": i + 4 
                })
            participants_cache = new_cache
            last_cache_update = time.time()
        except Exception as e: print(f"同步失敗: {e}")

@app.route('/')
def index(): return send_from_directory('.', '活動報到系統.html')

@app.route('/商品頁面.html')
def products_page(): return send_from_directory('.', '商品頁面.html')

@app.route('/api/config')
def get_config(): return jsonify(load_config())

@app.route('/api/search/<method>')
def search(method):
    refresh_cache()
    q = request.args.get(method, "").replace(" ", "")
    if method == 'phone': 
        q = ''.join(filter(str.isdigit, q))
        return jsonify({"success": True, "data": [p for p in participants_cache if ''.join(filter(str.isdigit, p["phone"])) == q]})
    return jsonify({"success": True, "data": [p for p in participants_cache if q.lower() in p.get(method, "").lower()]})

@app.route('/api/dashboard_stats')
def get_dashboard_stats():
    refresh_cache() # 確保數據是最新的
    config = load_config() # 讀取設定檔
    
    total_guests = len(participants_cache) # 總人數
    checked_in_list = [p for p in participants_cache if p['status'] == 'checked_in'] # 已報到名單
    checked_in_count = len(checked_in_list)
    
    # 計算餐食統計 (對應團體與個人報到)
    meal_stats = {}
    for p in checked_in_list:
        m = p.get('meal') or "未選擇"
        meal_stats[m] = meal_stats.get(m, 0) + 1
        
    return jsonify({
        "success": True,
        "activity_name": config.get('google_sheet_name', '未命名任務'), # 活動名稱
        "stats": {
            "total": total_guests,
            "checked_in": checked_in_count,
            "rate": f"{(checked_in_count / total_guests * 100):.1f}%" if total_guests > 0 else "0%", # 報到率
            "meals": meal_stats # 葷素統計
        },
        "last_sync": datetime.now().strftime('%H:%M:%S')
    })

@app.route('/api/checkin/<pid>', methods=['POST'])
def checkin(pid):
    data = request.json
    now_tw = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M:%S')
    p = next((x for x in participants_cache if x['id'] == pid), None)
    if not p: return jsonify({"success": False}), 404
    
    meal = data.get('meal', '未選擇')
    p['status'], p['meal'], p['checkedInAt'] = 'checked_in', meal, now_tw
    cols = load_config().get('excel_columns', {})
    
    # 精準寫入 14(N), 15(O), 16(P) 欄位
    updates = [
        {'range': gspread.utils.rowcol_to_a1(p['_row'], cols.get('checkedInAt', 14)), 'values': [[now_tw]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], cols.get('status', 15)), 'values': [['checked_in']]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], cols.get('meal', 16)), 'values': [[meal]]}
    ]
    threading.Thread(target=async_update_sheet, args=(updates,)).start()
    return jsonify({"success": True, "data": p})

@app.route('/api/checkin/batch', methods=['POST'])
def batch():
    data = request.json
    now_tw = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M:%S')
    cols = load_config().get('excel_columns', {})
    results, all_updates = [], []
    for item in data.get('selections', []):
        p = next((x for x in participants_cache if x['id'] == item['id']), None)
        if p:
            meal = item.get('meal', '葷食')
            p['status'], p['meal'], p['checkedInAt'] = 'checked_in', meal, now_tw
            results.append(p)
            all_updates.extend([
                {'range': gspread.utils.rowcol_to_a1(p['_row'], cols.get('checkedInAt', 14)), 'values': [[now_tw]]},
                {'range': gspread.utils.rowcol_to_a1(p['_row'], cols.get('status', 15)), 'values': [['checked_in']]},
                {'range': gspread.utils.rowcol_to_a1(p['_row'], cols.get('meal', 16)), 'values': [[meal]]}
            ])
    if all_updates: threading.Thread(target=async_update_sheet, args=(all_updates,)).start()
    return jsonify({"success": True, "data": results})

if __name__ == '__main__':
    refresh_cache(True)
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
