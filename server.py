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
# 核心設定：精確對齊你的試算表欄位
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 根據你的圖片規則：
# A(1):區域, B(2):階層, C(3):公司, F(6):姓名, H(8):手機, I(9):MAIL
# N(14):報到時間, O(15):狀態, P(16):餐食選擇
DEFAULT_CONFIG = {
    "show_meal_options": True,
    "google_sheet_name": "活動報到名單",
    "excel_columns": {
        "id": 6,           # 使用姓名(F欄)作為唯一標識
        "name": 6,         # F 欄
        "phone": 8,        # H 欄
        "company": 3,      # C 欄 (合併儲存格)
        "email": 9,        # I 欄
        "checkedInAt": 14, # N 欄：報到時間
        "status": 15,      # O 欄：狀態
        "meal": 16         # P 欄：餐食選擇
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

# =========================
# 修正後的 CACHE 邏輯：處理合併儲存格
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
            cols = DEFAULT_CONFIG["excel_columns"]
            new_cache = []
            
            current_company = ""
            # 從第四行開始讀取 (索引 3)，跳過前三行標題
            for row in all_values[3:]:
                def get(col_num):
                    idx = col_num - 1
                    return row[idx].strip() if idx < len(row) else ""
                
                # 處理合併的公司欄位
                temp_company = get(cols["company"])
                if temp_company:
                    current_company = temp_company
                
                name = get(cols["name"])
                if not name: continue # 沒有姓名就跳過

                new_cache.append({
                    "id": name,
                    "name": name,
                    "phone": get(cols["phone"]),
                    "company": current_company,
                    "email": get(cols["email"]),
                    "status": get(cols["status"]),
                    "meal": get(cols["meal"])
                })
            participants_cache = new_cache
            last_cache_update = now
            print(f"INFO: 快取更新成功，共 {len(new_cache)} 筆資料")
        except Exception as e:
            print("SYNC ERROR:", e)

# =========================
# API 區
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

@app.route('/api/search/company')
def search_company():
    refresh_cache()
    q = request.args.get("company", "").strip()
    result = [p for p in participants_cache if q in p["company"]]
    return jsonify({"success": True, "data": result})

# --- 修正後的個人報到 ---
@app.route('/api/checkin/<participant_id>', methods=['POST'])
def checkin(participant_id):
    try:
        data = request.json
        meal = data.get("meal", "未選擇")
        
        # 從快取中找出該學員的原始資料，確保回傳時公司名稱正確
        p_data = next((p for p in participants_cache if p["id"] == participant_id), None)
        company_name = p_data["company"] if p_data else "未知公司"

        sheet = get_worksheet()
        cols = DEFAULT_CONFIG["excel_columns"]
        cell = sheet.find(participant_id)
        if not cell: return jsonify({"success": False, "error": "找不到資料"}), 404
        
        row_idx = cell.row
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 寫入 N, O, P 欄位 (14, 15, 16)
        sheet.update_cell(row_idx, cols["checkedInAt"], now_str)
        sheet.update_cell(row_idx, cols["status"], "checked_in")
        sheet.update_cell(row_idx, cols["meal"], meal)

        refresh_cache(True)
        return jsonify({
            "success": True, 
            "data": {
                "name": participant_id, 
                "company": company_name, 
                "meal": meal, 
                "checkedInAt": now_str
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- 修正後的團體報到 ---
@app.route('/api/checkin/batch', methods=['POST'])
def checkin_batch():
    try:
        selections = request.json.get("selections", [])
        sheet = get_worksheet()
        cols = DEFAULT_CONFIG["excel_columns"]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        results = []
        for item in selections:
            cell = sheet.find(item["id"])
            if cell:
                sheet.update_cell(cell.row, cols["checkedInAt"], now_str)
                sheet.update_cell(cell.row, cols["status"], "checked_in")
                sheet.update_cell(cell.row, cols["meal"], item["meal"])
                # 獲取正確的公司名稱
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
