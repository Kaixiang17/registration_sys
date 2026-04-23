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
# 基本設定
# =============================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
KEY_PATH = os.path.join(BASE_DIR, 'test0417-493608-dce82b8c6901.json')

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

# =============================================
# Google Sheets
# =============================================
def get_gspread_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_PATH, scope)
    return gspread.authorize(creds)

def get_worksheet():
    config = load_config()
    client = get_gspread_client()
    spreadsheet = client.open(config['google_sheet_name'])
    return spreadsheet.get_worksheet(0)

# =============================================
# 快取同步（已修好）
# =============================================
def refresh_cache(force=False):
    global participants_cache, last_cache_update

    now = time.time()
    if not force and (now - last_cache_update < CACHE_TTL) and participants_cache:
        return

    with cache_lock:
        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在同步資料...")

            sheet = get_worksheet()
            all_values = sheet.get_all_values()

            # 🔍 DEBUG
            print("==== DEBUG START ====")
            print("總列數:", len(all_values))
            print("前3列:", all_values[:3])
            print("==== DEBUG END ====")

            if not all_values:
                print("❌ 沒抓到資料（可能權限問題）")
                return

            config = load_config()
            cols = config['excel_columns']

            # 找 header
            header_row_idx = 0
            for i, row in enumerate(all_values):
                if any("姓名" in str(cell) for cell in row):
                    header_row_idx = i
                    break

            rows = all_values[header_row_idx + 1:]

            new_cache = []
            last_values = {k: "" for k in cols.keys()}

            for row in rows:
                def get_val(col):
                    idx = col - 1
                    return str(row[idx]).strip() if idx < len(row) else ""

                current = {}
                for key, col in cols.items():
                    val = get_val(col)

                    if not val and key not in ['checkedInAt', 'status', 'meal', 'registeredAt']:
                        val = last_values[key]
                    else:
                        last_values[key] = val

                    current[key] = val

                p = {
                    "id": current['id'],
                    "name": current['name'],
                    "phone": current['phone'],
                    "company": current['company'],
                    "email": current['email'],
                    "qrCode": current['qrCode'],
                    "registeredAt": current['registeredAt'],
                    "checkedInAt": current['checkedInAt'] or None,
                    "status": current['status'] or 'registered',
                    "meal": current['meal']
                }

                if p['name']:
                    new_cache.append(p)

            participants_cache = new_cache
            last_cache_update = now

            print(f"✅ 同步完成：{len(participants_cache)} 筆")

        except Exception as e:
            print(f"❌ 同步失敗: {e}")

def background_sync():
    while True:
        time.sleep(CACHE_TTL)
        refresh_cache(force=True)

# =============================================
# API
# =============================================
@app.route('/')
def index():
    return send_from_directory('.', '活動報到系統.html')

@app.route('/api/participants')
def get_participants():
    refresh_cache()
    return jsonify({"success": True, "data": participants_cache})

@app.route('/api/search/phone')
def search_phone():
    refresh_cache()
    query = ''.join(filter(str.isdigit, request.args.get('phone', '')))
    results = []

    for p in participants_cache:
        phone = ''.join(filter(str.isdigit, str(p.get('phone', ''))))
        if phone == query:
            results.append(p)

    return jsonify({"success": True, "data": results})

@app.route('/api/search/name')
def search_name():
    refresh_cache()
    query = request.args.get('name', '').replace(" ", "").replace("　", "")
    results = []

    for p in participants_cache:
        name = str(p.get('name', '')).replace(" ", "").replace("　", "")
        if name == query:
            results.append(p)

    return jsonify({"success": True, "data": results})

# =============================================
# 啟動
# =============================================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))

    refresh_cache(force=True)
    threading.Thread(target=background_sync, daemon=True).start()

    app.run(host='0.0.0.0', port=port)
