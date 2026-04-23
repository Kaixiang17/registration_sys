import os
import json
import time
import threading
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# =========================
# 設定
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

participants_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
CACHE_TTL = 30

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

# =========================
# GOOGLE SHEETS
# =========================
def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    RENDER_SECRET_FILE = "/etc/secrets/google-creds.json"
    LOCAL_SECRET_FILE = os.path.join(BASE_DIR, "test0417-493608-dce82b8c6901.json")

    if os.path.exists(RENDER_SECRET_FILE):
        json_path = RENDER_SECRET_FILE
    else:
        json_path = LOCAL_SECRET_FILE

    creds = Credentials.from_service_account_file(json_path, scopes=scope)
    return gspread.authorize(creds)

def get_worksheet():
    config = load_config()
    client = get_gspread_client()
    sheet = client.open(config["google_sheet_name"])
    return sheet.get_worksheet(0)

# =========================
# CACHE 邏輯
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
            cols = config["excel_columns"]
            new_cache = []

            for row in all_values[1:]:
                def get(col):
                    i = col - 1
                    return row[i].strip() if i < len(row) else ""
                
                p = {
                    "id": get(cols["id"]),
                    "name": get(cols["name"]),
                    "phone": get(cols["phone"]),
                    "company": get(cols["company"]),
                    "email": get(cols["email"]),
                    "qrCode": get(cols["qrCode"]),
                    "registeredAt": get(cols["registeredAt"]),
                    "checkedInAt": get(cols["checkedInAt"]),
                    "status": get(cols["status"]) or "registered",
                    "meal": get(cols["meal"])
                }
                if p["name"]: new_cache.append(p)

            participants_cache = new_cache
            last_cache_update = now
        except Exception as e:
            print("SYNC ERROR:", e)

def background_sync():
    while True:
        time.sleep(CACHE_TTL)
        refresh_cache(True)

# =========================
# API 路由區
# =========================

@app.route('/')
def index():
    return send_from_directory('.', '活動報到系統.html')

@app.route('/api/config')
def get_api_config():
    return jsonify({"success": True, "show_meal_options": True})

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

@app.route('/api/search/email')
def search_email():
    refresh_cache()
    q = request.args.get("email", "").strip().lower()
    result = [p for p in participants_cache if p["email"].strip().lower() == q]
    return jsonify({"success": True, "data": result})

@app.route('/api/search/company')
def search_company():
    refresh_cache()
    q = request.args.get("company", "").strip()
    result = [p for p in participants_cache if q in p["company"]]
    return jsonify({"success": True, "data": result})

# --- 重點：個人報到 API ---
@app.route('/api/checkin/<participant_id>', methods=['POST'])
def checkin(participant_id):
    try:
        data = request.json
        meal = data.get("meal", "")
        
        sheet = get_worksheet()
        config = load_config()
        cols = config["excel_columns"]

        cell = sheet.find(participant_id)
        if not cell: return jsonify({"success": False, "error": "找不到 ID"}), 404

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.update_cell(cell.row, cols["checkedInAt"], now_str)
        sheet.update_cell(cell.row, cols["status"], "checked_in")
        if meal: sheet.update_cell(cell.row, cols["meal"], meal)

        refresh_cache(True)
        # 回傳給前端需要的成功格式
        return jsonify({
            "success": True, 
            "data": {"name": data.get("name"), "company": "更新中", "meal": meal, "checkedInAt": now_str}
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- 重點：團體報到 API ---
@app.route('/api/checkin/batch', methods=['POST'])
def checkin_batch():
    try:
        selections = request.json.get("selections", [])
        sheet = get_worksheet()
        config = load_config()
        cols = config["excel_columns"]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        results = []
        for item in selections:
            cell = sheet.find(item["id"])
            if cell:
                sheet.update_cell(cell.row, cols["checkedInAt"], now_str)
                sheet.update_cell(cell.row, cols["status"], "checked_in")
                if item["meal"]: sheet.update_cell(cell.row, cols["meal"], item["meal"])
                results.append({"name": item["name"], "meal": item["meal"], "company": "團體報到"})

        refresh_cache(True)
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# =========================
# 啟動伺服器
# =========================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    refresh_cache(True)
    threading.Thread(target=background_sync, daemon=True).start()
    app.run(host="0.0.0.0", port=port)
