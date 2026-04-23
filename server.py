import os
import json
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials # 建議使用新版庫

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 優先讀取 Render 的 Secret File
RENDER_KEY = "/etc/secrets/google-creds.json"
LOCAL_KEY = os.path.join(BASE_DIR, 'test0417-493608-dce82b8c6901.json')

participants_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
CACHE_TTL = 300 

def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_path = RENDER_KEY if os.path.exists(RENDER_KEY) else LOCAL_KEY
    creds = Credentials.from_service_account_file(json_path, scopes=scope)
    return gspread.authorize(creds)

def get_worksheet():
    config = load_config()
    client = get_gspread_client()
    return client.open(config['google_sheet_name']).get_worksheet(0)

# 背景寫入函式：提高回傳效率的關鍵
def async_update_sheet(updates):
    try:
        sheet = get_worksheet()
        sheet.batch_update(updates)
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
            cols = load_config()['excel_columns']
            new_cache = []
            last_company = ""
            # 處理合併儲存格與向下填充
            for i, row in enumerate(all_values[3:]):
                def g(c): return row[c-1].strip() if c-1 < len(row) else ""
                comp = g(cols['company'])
                if comp: last_company = comp
                name = g(cols['name'])
                if not name: continue
                new_cache.append({
                    "id": name, "name": name, "phone": g(cols['phone']),
                    "company": last_company, "email": g(cols['email']),
                    "status": g(cols['status']), "meal": g(cols['meal']),
                    "_row": i + 4 # 記住行號，報到時直接定位
                })
            participants_cache = new_cache
            last_cache_update = time.time()
        except Exception as e: print(f"同步失敗: {e}")

@app.route('/')
def index(): return send_from_directory('.', '活動報到系統.html')

@app.route('/商品頁面.html')
def product_page(): return send_from_directory('.', '商品頁面.html')

@app.route('/api/config')
def get_config(): return jsonify(load_config())

# 報到 API：秒級回傳邏輯
@app.route('/api/checkin/<participant_id>', methods=['POST'])
def checkin(participant_id):
    data = request.json
    meal = data.get('meal', '未選擇')
    now_tw = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M:%S')
    
    p = next((x for x in participants_cache if x['id'] == participant_id), None)
    if not p: return jsonify({"success": False, "error": "找不到參加者"}), 404

    # 1. 立即更新本地快取
    p['status'] = 'checked_in'
    p['meal'] = meal
    
    # 2. 準備背景寫入指令 (對應 N, O, P 欄)
    cols = load_config()['excel_columns']
    updates = [
        {'range': gspread.utils.rowcol_to_a1(p['_row'], cols['checkedInAt']), 'values': [[now_tw]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], cols['status']), 'values': [['checked_in']]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], cols['meal']), 'values': [[meal]]}
    ]
    threading.Thread(target=async_update_sheet, args=(updates,)).start()
    
    return jsonify({"success": True, "data": p}) # 3. 直接回傳，不等待寫入

# 其餘搜尋 API (search_name, search_phone, search_company) 略...

if __name__ == '__main__':
    refresh_cache(True)
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
