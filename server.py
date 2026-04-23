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

# =========================
# 您的核心設定 (精確對齊 N, O, P)
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 優先讀取 Render 密鑰檔案路徑
RENDER_KEY = "/etc/secrets/google-creds.json"
LOCAL_KEY = os.path.join(BASE_DIR, 'test0417-493608-dce82b8c6901.json')

participants_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
CACHE_TTL = 300 

def load_config():
    config_path = os.path.join(BASE_DIR, 'config.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {"google_sheet_name": "活動報到名單"}
    return {"google_sheet_name": "活動報到名單"}

def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_path = RENDER_KEY if os.path.exists(RENDER_KEY) else LOCAL_KEY
    creds = Credentials.from_service_account_file(json_path, scopes=scope)
    return gspread.authorize(creds)

def get_worksheet():
    config = load_config()
    client = get_gspread_client()
    return client.open(config.get('google_sheet_name')).get_worksheet(0)

# 提高效率：背景寫入邏輯
def async_update_sheet(updates):
    try:
        sheet = get_worksheet()
        sheet.batch_update(updates)
        print("背景寫入成功")
    except Exception as e:
        print(f"背景寫入失敗: {e}")

def refresh_cache(force=False):
    global participants_cache, last_cache_update
    if not force and (time.time() - last_cache_update < CACHE_TTL) and participants_cache:
        return
    with cache_lock:
        try:
            sheet = get_worksheet()
            all_values = sheet.get_all_values()
            if not all_values: return
            
            # 使用您的欄位規則：姓名F(6), 手機H(8), 公司C(3), EMAIL I(9)
            cols = {"id": 6, "name": 6, "phone": 8, "company": 3, "email": 9}
            new_cache = []
            last_company = ""

            for i, row in enumerate(all_values[3:]): # 跳過前三行標題
                def g(c): return row[c-1].strip() if c-1 < len(row) else ""
                comp = g(cols['company'])
                if comp: last_company = comp
                name = g(cols['name'])
                if not name: continue
                
                new_cache.append({
                    "id": name, "name": name, "phone": g(cols['phone']),
                    "company": last_company, "email": g(cols['email']),
                    "status": row[14].strip() if len(row) > 14 else "", 
                    "meal": row[15].strip() if len(row) > 15 else "",
                    "_row": i + 4 
                })
            participants_cache = new_cache
            last_cache_update = time.time()
        except Exception as e:
            print(f"快取更新錯誤: {e}")

# =========================
# API 路由
# =========================
@app.route('/')
def index():
    return send_from_directory('.', '活動報到系統.html')

@app.route('/商品頁面.html')
def product_page():
    return send_from_directory('.', '商品頁面.html')

@app.route('/api/config')
def get_config():
    return jsonify(load_config())

@app.route('/api/search/<method>')
def search(method):
    refresh_cache()
    q = request.args.get(method, "").replace(" ", "")
    if method == 'phone': 
        q = ''.join(filter(str.isdigit, q))
        return jsonify({"success": True, "data": [p for p in participants_cache if ''.join(filter(str.isdigit, p["phone"])) == q]})
    return jsonify({"success": True, "data": [p for p in participants_cache if q.lower() in p.get(method, "").lower()]})

@app.route('/api/checkin/<pid>', methods=['POST'])
def checkin(pid):
    data = request.json
    now_tw = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M:%S')
    p = next((x for x in participants_cache if x['id'] == pid), None)
    if not p: return jsonify({"success": False}), 404

    meal = data.get('meal', '未選擇')
    p['status'], p['meal'], p['checkedInAt'] = 'checked_in', meal, now_tw
    
    # 寫入 N(14), O(15), P(16) 欄位
    updates = [
        {'range': gspread.utils.rowcol_to_a1(p['_row'], 14), 'values': [[now_tw]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], 15), 'values': [['checked_in']]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], 16), 'values': [[meal]]}
    ]
    threading.Thread(target=async_update_sheet, args=(updates,)).start()
    return jsonify({"success": True, "data": p})

if __name__ == '__main__':
    refresh_cache(True)
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
