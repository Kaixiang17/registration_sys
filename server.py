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
# 核心設定 (保留你的 N, O, P 欄位對齊)
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

participants_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
CACHE_TTL = 30 

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

# =========================
# Google Sheets 邏輯 (保留你的 Render 路徑判斷)
# =========================
def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    RENDER_SECRET_FILE = "/etc/secrets/google-creds.json"
    LOCAL_SECRET_FILE = os.path.join(BASE_DIR, "test0417-493608-dce82b8c6901.json")
    
    json_path = RENDER_SECRET_FILE if os.path.exists(RENDER_SECRET_FILE) else LOCAL_SECRET_FILE
    creds = Credentials.from_service_account_file(json_path, scopes=scope)
    return gspread.authorize(creds)

def get_worksheet():
    config = load_config()
    client = get_gspread_client()
    sheet = client.open(config.get("google_sheet_name", "活動報到名單"))
    return sheet.get_worksheet(0)

# =========================
# 修正後的快取邏輯 (保留你的合併儲存格向下填充)
# =========================
def refresh_cache(force=False):
    global participants_cache, last_cache_update
    now = time.time()
    if not force and (now - last_cache_update < CACHE_TTL) and participants_cache:
        return
    
    with cache_lock:
        try:
            sheet = get_worksheet()
            all_values = sheet.get_all_values()
            if not all_values: return
            
            config = load_config()
            cols = config.get("excel_columns", {})
            new_cache = []
            
            last_company = ""
            
            # 從第 4 行開始 (索引 3)，跳過標題
            for row in all_values[3:]:
                def get_val(col_idx):
                    idx = col_idx - 1
                    return row[idx].strip() if idx < len(row) else ""
                
                # 合併儲存格處理：若當前列公司名為空，則延用上一列
                comp = get_val(cols.get("company", 3))
                if comp: last_company = comp
                
                name = get_val(cols.get("name", 6))
                if not name: continue
                
                new_cache.append({
                    "id": name, 
                    "name": name,
                    "phone": get_val(cols.get("phone", 8)),
                    "company": last_company,
                    "email": get_val(cols.get("email", 9)),
                    "status": get_val(cols.get("status", 15)),
                    "meal": get_val(cols.get("meal", 16)),
                    "checkedInAt": get_val(cols.get("checkedInAt", 14))
                })
            
            participants_cache = new_cache
            last_cache_update = now
            print(f"INFO: 快取更新完成，共 {len(new_cache)} 人")
        except Exception as e:
            print(f"SYNC ERROR: {e}")

def background_sync():
    while True:
        time.sleep(CACHE_TTL)
        refresh_cache(True)

# =========================
# API 路由
# =========================

@app.route('/')
def index():
    return send_from_directory('.', '活動報到系統.html')

# 同事新增的商品頁面路由
@app.route('/商品頁面.html')
def products_page():
    return send_from_directory('.', '商品頁面.html')

@app.route('/api/config')
def get_api_config():
    return jsonify(load_config())

@app.route('/api/search/name')
def search_name():
    refresh_cache()
    q = request.args.get("name", "").replace(" ", "")
    result = [p for p in participants_cache if p["name"].replace(" ", "") == q]
    return jsonify({"success": True, "data": result})

@app.route('/api/search/phone')
def search_phone():
    refresh_cache()
    q = ''.join(filter(str.isdigit, request.args.get("phone", "")))
    result = [p for p in participants_cache if ''.join(filter(str.isdigit, p["phone"])) == q]
    return jsonify({"success": True, "data": result})

@app.route('/api/search/company')
def search_company():
    refresh_cache()
    q = request.args.get("company", "").strip()
    result = [p for p in participants_cache if q.lower() in p["company"].lower()]
    return jsonify({"success": True, "data": result})

# 個人報到 (保留你的台灣時間 +8 小時)
@app.route('/api/checkin/<participant_id>', methods=['POST'])
def checkin(participant_id):
    try:
        data = request.json
        meal = data.get("meal", "未選擇")
        
        sheet = get_worksheet()
        config = load_config()
        cols = config.get("excel_columns", {})
        
        cell = sheet.find(participant_id)
        if not cell: return jsonify({"success": False, "error": "找不到資料"}), 404
        
        # 強制修正為台灣時間
        now_taiwan = datetime.utcnow() + timedelta(hours=8)
        now_str = now_taiwan.strftime("%Y-%m-%d %H:%M:%S")
        
        # 寫入 N, O, P 欄位 (14, 15, 16)
        sheet.update_cell(cell.row, cols["checkedInAt"], now_str)
        sheet.update_cell(cell.row, cols["status"], "checked_in")
        sheet.update_cell(cell.row, cols["meal"], meal)
        
        refresh_cache(True)
        p_info = next((p for p in participants_cache if p["id"] == participant_id), {})
        return jsonify({
            "success": True, 
            "data": {
                "name": participant_id, 
                "company": p_info.get("company"), 
                "meal": meal, 
                "checkedInAt": now_str
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# 團體報到
@app.route('/api/checkin/batch', methods=['POST'])
def checkin_batch():
    try:
        selections = request.json.get("selections", [])
        sheet = get_worksheet()
        config = load_config()
        cols = config.get("excel_columns", {})
        
        now_taiwan = datetime.utcnow() + timedelta(hours=8)
        now_str = now_taiwan.strftime("%Y-%m-%d %H:%M:%S")
        
        results = []
        for item in selections:
            cell = sheet.find(item["id"])
            if cell:
                sheet.update_cell(cell.row, cols["checkedInAt"], now_str)
                sheet.update_cell(cell.row, cols["status"], "checked_in")
                sheet.update_cell(cell.row, cols["meal"], item["meal"])
                
                p_info = next((p for p in participants_cache if p["id"] == item["id"]), {})
                results.append({
                    "name": item["name"], 
                    "meal": item["meal"], 
                    "company": p_info.get("company"), 
                    "checkedInAt": now_str
                })
        
        refresh_cache(True)
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    refresh_cache(True)
    threading.Thread(target=background_sync, daemon=True).start()
    app.run(host="0.0.0.0", port=port)
