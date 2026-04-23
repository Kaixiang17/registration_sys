import os
import json
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# =============================================
# 系統設定與全域變數
# =============================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

# 快取相關變數
participants_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
CACHE_TTL = 300  # 快取存活時間（秒）

DEFAULT_CONFIG = {
    "show_meal_options": True,
    "google_sheet_name": "活動報到名單",
    "excel_columns": {
        "id": 1, "name": 2, "phone": 3, "company": 4, "email": 5,
        "qrCode": 6, "registeredAt": 7, "checkedInAt": 8, "status": 9, "meal": 10
    }
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return DEFAULT_CONFIG
    return DEFAULT_CONFIG

# =============================================
# Google Sheets 核心邏輯
# =============================================
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    # 支援 Render 雲端與本地端金鑰
    RENDER_SECRET_FILE = "/etc/secrets/google-creds.json"
    LOCAL_SECRET_FILE = os.path.join(BASE_DIR, 'test0417-493608-4c80e7362606.json')
    json_path = RENDER_SECRET_FILE if os.path.exists(RENDER_SECRET_FILE) else LOCAL_SECRET_FILE
    creds = ServiceAccountCredentials.from_json_keyfile_name(json_path, scope)
    return gspread.authorize(creds)

def get_worksheet():
    config = load_config()
    client = get_gspread_client()
    spreadsheet = client.open(config['google_sheet_name'])
    return spreadsheet.get_worksheet(0)

# 背景更新 Google Sheet 的獨立函式 (大幅提升報到速度)
def async_update_sheet(updates):
    try:
        sheet = get_worksheet()
        sheet.batch_update(updates)
        print(f"[{datetime.utcnow() + timedelta(hours=8)}] 背景更新 Google Sheet 成功")
    except Exception as e:
        print(f"背景更新失敗: {e}")

def refresh_cache(force=False):
    global participants_cache, last_cache_update
    now = time.time()
    
    if not force and (now - last_cache_update < CACHE_TTL) and participants_cache:
        return

    with cache_lock:
        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在從 Google Sheets 同步資料...")
            sheet = get_worksheet()
            all_values = sheet.get_all_values()
            if not all_values: return

            config = load_config()
            cols = config['excel_columns']
            
            header_row_idx = 0
            for i, row in enumerate(all_values):
                if any("姓名" in str(cell) for cell in row):
                    header_row_idx = i
                    break
            
            rows = all_values[header_row_idx + 1:]
            new_cache = []
            last_values = {k: "" for k in cols.keys()}
            
            for i, row in enumerate(rows):
                def get_raw_val(col_idx):
                    idx = col_idx - 1
                    return str(row[idx]).strip() if idx < len(row) else ""

                current_p_data = {}
                for key, col_idx in cols.items():
                    val = get_raw_val(col_idx)
                    if not val and key not in ['checkedInAt', 'status', 'meal', 'registeredAt']:
                        val = last_values[key]
                    else:
                        last_values[key] = val
                    current_p_data[key] = val

                p = {
                    "id": current_p_data['id'],
                    "name": current_p_data['name'],
                    "phone": current_p_data['phone'],
                    "company": current_p_data['company'],
                    "email": current_p_data['email'],
                    "qrCode": current_p_data['qrCode'],
                    "registeredAt": current_p_data['registeredAt'],
                    "checkedInAt": current_p_data['checkedInAt'] if current_p_data['checkedInAt'] not in ['', 'None'] else None,
                    "status": current_p_data['status'] if current_p_data['status'] else 'registered',
                    "meal": current_p_data['meal'],
                    # 記住真實行數，免去報到時的重新搜尋！
                    "_row_idx": header_row_idx + 2 + i 
                }
                
                if p['name']:
                    new_cache.append(p)
            
            participants_cache = new_cache
            last_cache_update = now
            print(f"同步完成，共 {len(participants_cache)} 筆資料。")
        except Exception as e:
            print(f"同步資料失敗: {e}")

def background_sync():
    while True:
        time.sleep(CACHE_TTL)
        refresh_cache(force=True)

# =============================================
# API 路由
# =============================================
@app.route('/')
def index(): return send_from_directory('.', '活動報到系統.html')

@app.route('/商品頁面.html')
def products_page(): return send_from_directory('.', '商品頁面.html')

@app.route('/api/config')
def get_config(): return jsonify(load_config())

@app.route('/api/participants')
def get_participants():
    refresh_cache()
    return jsonify({"success": True, "data": participants_cache})

@app.route('/api/search/phone')
def search_phone():
    refresh_cache()
    phone = request.args.get('phone', '').strip()
    return jsonify({"success": True, "data": [p for p in participants_cache if p['phone'] == phone]})

@app.route('/api/search/name')
def search_name():
    refresh_cache()
    name = request.args.get('name', '').strip()
    return jsonify({"success": True, "data": [p for p in participants_cache if p['name'] == name]})

@app.route('/api/search/email')
def search_email():
    refresh_cache()
    email = request.args.get('email', '').strip().lower()
    return jsonify({"success": True, "data": [p for p in participants_cache if p['email'].lower() == email]})

@app.route('/api/search/company')
def search_company():
    refresh_cache()
    company = request.args.get('company', '').strip()
    return jsonify({"success": True, "data": [p for p in participants_cache if company.lower() in p['company'].lower()]})

# --- 個人報到 ---
@app.route('/api/checkin/<participant_id>', methods=['POST'])
def checkin(participant_id):
    data = request.json
    meal = data.get('meal', '').strip()
    p_name = data.get('name', '').strip()
    
    try:
        config = load_config()
        cols = config['excel_columns']
        now_taiwan = datetime.utcnow() + timedelta(hours=8)
        now_str = now_taiwan.strftime('%Y/%m/%d %H:%M:%S')
        
        row_idx = None
        returned_p = None
        
        # 1. 瞬間更新記憶體快取
        with cache_lock:
            for p in participants_cache:
                if p['id'] == participant_id and (not p_name or p['name'] == p_name):
                    p['status'] = 'checked_in'
                    p['checkedInAt'] = now_str
                    p['meal'] = meal
                    row_idx = p['_row_idx']
                    returned_p = p
                    break
        
        if not row_idx:
            return jsonify({"success": False, "error": "找不到該參加者"}), 404
        
        # 2. 將 Google Sheet 寫入丟到背景執行 (提速關鍵)
        updates = [
            {'range': gspread.utils.rowcol_to_a1(row_idx, cols['checkedInAt']), 'values': [[now_str]]},
            {'range': gspread.utils.rowcol_to_a1(row_idx, cols['status']), 'values': [['已報到']]}
        ]
        if config['show_meal_options']:
            updates.append({'range': gspread.utils.rowcol_to_a1(row_idx, cols['meal']), 'values': [[meal]]})
            
        threading.Thread(target=async_update_sheet, args=(updates,)).start()
        
        # 3. 秒回傳給前端
        return jsonify({"success": True, "data": returned_p})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- 批次報到 ---
@app.route('/api/checkin/batch', methods=['POST'])
def batch_checkin():
    data = request.json
    selections = data.get('selections', [])
    if not selections: return jsonify({"success": False, "error": "未提供參加者資料"}), 400
        
    try:
        config = load_config()
        cols = config['excel_columns']
        now_taiwan = datetime.utcnow() + timedelta(hours=8)
        now_str = now_taiwan.strftime('%Y/%m/%d %H:%M:%S')
        
        results = []
        all_updates = []
        
        # 1. 瞬間更新記憶體快取
        with cache_lock:
            for item in selections:
                for p in participants_cache:
                    if p['id'] == item['id'] and p['name'] == item['name']:
                        p['status'] = 'checked_in'
                        p['checkedInAt'] = now_str
                        p['meal'] = item.get('meal', '').strip()
                        results.append(p)
                        
                        row_idx = p['_row_idx']
                        all_updates.extend([
                            {'range': gspread.utils.rowcol_to_a1(row_idx, cols['checkedInAt']), 'values': [[now_str]]},
                            {'range': gspread.utils.rowcol_to_a1(row_idx, cols['status']), 'values': [['已報到']]}
                        ])
                        if config['show_meal_options']:
                            all_updates.append({'range': gspread.utils.rowcol_to_a1(row_idx, cols['meal']), 'values': [[p['meal']]]})
                        break
        
        # 2. 將 Google Sheet 寫入丟到背景執行
        if all_updates:
            threading.Thread(target=async_update_sheet, args=(all_updates,)).start()
            
        # 3. 秒回傳
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    print("活動報到系統啟動中...")
    refresh_cache(force=True)
    threading.Thread(target=background_sync, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)), debug=False)
