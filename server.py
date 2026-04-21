import os
import json
import time
import threading
from datetime import datetime
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
KEY_PATH = os.path.join(BASE_DIR, 'test0417-493608-dce82b8c6901.json')

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
    creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_PATH, scope)
    return gspread.authorize(creds)

def get_worksheet():
    config = load_config()
    client = get_gspread_client()
    spreadsheet = client.open(config['google_sheet_name'])
    try:
        # 優先嘗試讀取第一個分頁，這通常是資料所在
        return spreadsheet.get_worksheet(0)
    except:
        return spreadsheet.get_worksheet(0)

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
            if not all_values:
                return

            config = load_config()
            cols = config['excel_columns']
            
            # 找出標題列（通常是第一列，但為了保險可以搜尋包含「姓名」的列）
            header_row_idx = 0
            for i, row in enumerate(all_values):
                if any("姓名" in str(cell) for cell in row):
                    header_row_idx = i
                    break
            
            rows = all_values[header_row_idx + 1:]
            new_cache = []
            
            # 用於向下填充的暫存器
            last_values = {k: "" for k in cols.keys()}
            
            for row in rows:
                def get_raw_val(col_idx):
                    idx = col_idx - 1
                    return str(row[idx]).strip() if idx < len(row) else ""

                # 核心邏輯：對所有關鍵欄位進行向下填充
                # 這樣即使 ID、公司名稱、電話等被合併，也能正確抓到
                current_p_data = {}
                for key, col_idx in cols.items():
                    val = get_raw_val(col_idx)
                    # 如果當前儲存格為空，且不是報到時間/狀態/餐食等動態欄位，則向下填充
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
                    "meal": current_p_data['meal']
                }
                
                # 只要有姓名，就視為有效人員
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
def index():
    return send_from_directory('.', '活動報到系統.html')

@app.route('/api/config')
def get_config():
    return jsonify(load_config())

@app.route('/api/participants')
def get_participants():
    refresh_cache()
    return jsonify({"success": True, "data": participants_cache})

@app.route('/api/search/phone')
def search_phone():
    refresh_cache()
    phone = request.args.get('phone', '').strip()
    results = [p for p in participants_cache if p['phone'] == phone]
    return jsonify({"success": True, "data": results})

@app.route('/api/search/name')
def search_name():
    refresh_cache()
    name = request.args.get('name', '').strip()
    results = [p for p in participants_cache if p['name'] == name]
    return jsonify({"success": True, "data": results})

@app.route('/api/search/email')
def search_email():
    refresh_cache()
    email = request.args.get('email', '').strip().lower()
    results = [p for p in participants_cache if p['email'].lower() == email]
    return jsonify({"success": True, "data": results})

@app.route('/api/search/company')
def search_company():
    refresh_cache()
    company = request.args.get('company', '').strip()
    results = [p for p in participants_cache if company.lower() in p['company'].lower()]
    return jsonify({"success": True, "data": results})

@app.route('/api/checkin/<participant_id>', methods=['POST'])
def checkin(participant_id):
    data = request.json
    meal = data.get('meal', '').strip()
    p_name = data.get('name', '').strip() # 增加姓名輔助定位
    
    try:
        sheet = get_worksheet()
        config = load_config()
        cols = config['excel_columns']
        
        all_values = sheet.get_all_values()
        id_col_idx = cols['id'] - 1
        name_col_idx = cols['name'] - 1
        
        row_idx = -1
        last_id = ""
        for i, row in enumerate(all_values):
            if i == 0: continue
            curr_id = row[id_col_idx].strip() if id_col_idx < len(row) else ""
            if not curr_id: curr_id = last_id
            else: last_id = curr_id
            
            curr_name = row[name_col_idx].strip() if name_col_idx < len(row) else ""
            
            # 同時比對 ID 與 姓名，確保在合併儲存格中找對人
            if curr_id == participant_id and (not p_name or curr_name == p_name):
                row_idx = i + 1
                break
        
        if row_idx == -1:
            return jsonify({"success": False, "error": "找不到該參加者"}), 404
        
        now_str = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
        updates = [
            {'range': gspread.utils.rowcol_to_a1(row_idx, cols['checkedInAt']), 'values': [[now_str]]},
            {'range': gspread.utils.rowcol_to_a1(row_idx, cols['status']), 'values': [['checked_in']]}
        ]
        if config['show_meal_options']:
            updates.append({'range': gspread.utils.rowcol_to_a1(row_idx, cols['meal']), 'values': [[meal]]})
            
        sheet.batch_update(updates)
        
        with cache_lock:
            for p in participants_cache:
                if p['id'] == participant_id and (not p_name or p['name'] == p_name):
                    p['status'] = 'checked_in'
                    p['checkedInAt'] = now_str
                    p['meal'] = meal
                    return jsonify({"success": True, "data": p})
        
        return jsonify({"success": True})
    except Exception as e:
        print(f"報到失敗: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/checkin/batch', methods=['POST'])
def batch_checkin():
    data = request.json
    selections = data.get('selections', [])
    
    if not selections:
        return jsonify({"success": False, "error": "未提供參加者資料"}), 400
        
    try:
        sheet = get_worksheet()
        config = load_config()
        cols = config['excel_columns']
        now_str = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
        
        all_values = sheet.get_all_values()
        id_col_idx = cols['id'] - 1
        name_col_idx = cols['name'] - 1
        
        # 建立 (ID, 姓名) 到行號的映射
        lookup_to_row = {}
        last_id = ""
        for i, row in enumerate(all_values):
            if i == 0: continue
            curr_id = row[id_col_idx].strip() if id_col_idx < len(row) else ""
            if not curr_id: curr_id = last_id
            else: last_id = curr_id
            
            curr_name = row[name_col_idx].strip() if name_col_idx < len(row) else ""
            if curr_id and curr_name:
                lookup_to_row[(curr_id, curr_name)] = i + 1
        
        results = []
        all_updates = []
        
        with cache_lock:
            for item in selections:
                pid = item['id']
                pname = item['name']
                p_meal = item.get('meal', '').strip()
                
                key = (pid, pname)
                if key in lookup_to_row:
                    row_idx = lookup_to_row[key]
                    all_updates.extend([
                        {'range': gspread.utils.rowcol_to_a1(row_idx, cols['checkedInAt']), 'values': [[now_str]]},
                        {'range': gspread.utils.rowcol_to_a1(row_idx, cols['status']), 'values': [['checked_in']]}
                    ])
                    if config['show_meal_options']:
                        all_updates.append({'range': gspread.utils.rowcol_to_a1(row_idx, cols['meal']), 'values': [[p_meal]]})
                    
                    for p in participants_cache:
                        if p['id'] == pid and p['name'] == pname:
                            p['status'] = 'checked_in'
                            p['checkedInAt'] = now_str
                            p['meal'] = p_meal
                            results.append(p)
                            break
        
        if all_updates:
            sheet.batch_update(all_updates)
            
        return jsonify({"success": True, "data": results})
    except Exception as e:
        print(f"批次報到失敗: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    print("活動報到系統啟動中...")
    refresh_cache(force=True)
    threading.Thread(target=background_sync, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
