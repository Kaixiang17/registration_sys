import os
import json
import time
import threading
from datetime import datetime, timedelta  # 這裡加入了 timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# =========================
# 核心設定：精確對齊你的試算表欄位
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "show_meal_options": True,
    "google_sheet_name": "活動報到名單",
    "excel_columns": {
        "id": 6,           # F 欄
        "name": 6,         # F 欄
        "phone": 8,        # H 欄
        "company": 3,      # C 欄
        "email": 9,        # I 欄
        "checkedInAt": 14, # N 欄
        "status": 15,      # O 欄
        "meal": 16         # P 欄
    }
}

participants_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
CACHE_TTL = 30

def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    RENDER_SECRET_FILE = "/etc/secrets/google-creds.json"
    LOCAL_SECRET_FILE = os.path.join(BASE_DIR, "test0417-493608-dce82b8c6901.json")
    json_path = RENDER_SECRET_FILE if os.path.exists(RENDER_SECRET_FILE) else LOCAL_SECRET_FILE
    creds = Credentials.from_service_account_file(json_path, scopes=scope)
    return gspread.authorize(creds)

def get_worksheet():
    client = get_gspread_client()
    sheet = client.open(DEFAULT_CONFIG["google_sheet_name"])
    return sheet.get_worksheet(0)

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
            cols = DEFAULT_CONFIG["excel_columns"]
            new_cache = []
            current_company = ""
            for row in all_values[3:]:
                def get(col_num):
                    idx = col_num - 1
                    return row[idx].strip() if idx < len(row) else ""
                temp_company = get(cols["company"])
                if temp_company: current_company = temp_company
                name = get(cols["name"])
                if not name: continue
                new_cache.append({
                    "id": name, "name": name, "phone": get(cols["phone"]),
                    "company": current_company, "email": get(cols["email"]),
                    "status": get(cols["status"]), "meal": get(cols["meal"])
                })
            participants_cache = new_cache
            last_cache_update = now
            print(f"INFO: 快取更新成功，共 {len(new_cache)} 筆資料")
        except Exception as e: print("SYNC ERROR:", e)

def background_sync():
    while True:
        time.sleep(CACHE_TTL); refresh_cache(True)

@app.route('/')
def index(): return send_from_directory('.', '活動報到系統.html')

@app.route('/api/config')
def get_api_config(): return jsonify({"success": True, "show_meal_options": True})

@app.route('/api/search/name')
def search_name():
    refresh_cache()
    q = request.args.get("name", "").replace(" ", "").replace("　", "")
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
    result = [p for p in participants_cache if q in p["company"]]
    return jsonify({"success": True, "data": result})

# --- 個人報到 (修正時區) ---
@app.route('/api/checkin/<participant_id>', methods=['POST'])
def checkin(participant_id):
    try:
        data = request.json
        meal = data.get("meal", "未選擇")
        p_data = next((p for p in participants_cache if p["id"] == participant_id), None)
        company_name = p_data["company"] if p_data else "未知公司"

        sheet = get_worksheet()
        cols = DEFAULT_CONFIG["excel_columns"]
        cell = sheet.find(participant_id)
        if not cell: return jsonify({"success": False, "error": "找不到資料"}), 404
        
        row_idx = cell.row
        
        # 修正：強制轉換為台灣時間 (UTC+8)
        now_taiwan = datetime.utcnow() + timedelta(hours=8)
        now_str = now_taiwan.strftime("%Y-%m-%d %H:%M:%S")
        
        sheet.update_cell(row_idx, cols["checkedInAt"], now_str)
        sheet.update_cell(row_idx, cols["status"], "checked_in")
        sheet.update_cell(row_idx, cols["meal"], meal)

        refresh_cache(True)
        return jsonify({"success": True, "data": {"name": participant_id, "company": company_name, "meal": meal, "checkedInAt": now_str}})
    except Exception as e: return jsonify({"success": False, "error": str(e)}), 500

# --- 團體報到 (修正時區) ---
@app.route('/api/checkin/batch', methods=['POST'])
def checkin_batch():
    try:
        selections = request.json.get("selections", [])
        sheet = get_worksheet()
        cols = DEFAULT_CONFIG["excel_columns"]
        
        # 修正：強制轉換為台灣時間 (UTC+8)
        now_taiwan = datetime.utcnow() + timedelta(hours=8)
        now_str = now_taiwan.strftime("%Y-%m-%d %H:%M:%S")
        
        results = []
        for item in selections:
            cell = sheet.find(item["id"])
            if cell:
                sheet.update_cell(cell.row, cols["checkedInAt"], now_str)
                sheet.update_cell(cell.row, cols["status"], "checked_in")
                sheet.update_cell(cell.row, cols["meal"], item["meal"])
                p_info = next((p for p in participants_cache if p["id"] == item["id"]), {"company": "團體"})
                results.append({"name": item["name"], "meal": item["meal"], "company": p_info["company"], "checkedInAt": now_str})
        refresh_cache(True)
        return jsonify({"success": True, "data": results})
    except Exception as e: return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    refresh_cache(True)
    threading.Thread(target=lambda: (time.sleep(5), background_sync()), daemon=True).start()
    app.run(host="0.0.0.0", port=port)
