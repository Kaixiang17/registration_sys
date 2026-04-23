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

# =============================================
# 系統設定與全域變數
# =============================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

# 支援 Render 雲端與本地端金鑰
RENDER_KEY = "/etc/secrets/google-creds.json"
LOCAL_KEY = os.path.join(BASE_DIR, 'test0417-493608-dce82b8c6901.json')

# 快取相關變數
participants_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
CACHE_TTL = 300  # 快取存活時間（秒）

def load_config():
    """載入設定檔"""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

# =============================================
# Google Sheets 核心邏輯
# =============================================
def get_gspread_client():
    """取得 Google Sheets 客戶端，自動判斷金鑰路徑"""
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_path = RENDER_KEY if os.path.exists(RENDER_KEY) else LOCAL_KEY
    creds = Credentials.from_service_account_file(json_path, scopes=scope)
    return gspread.authorize(creds)

def get_worksheet():
    """取得目標工作表"""
    config = load_config()
    client = get_gspread_client()
    # 根據 config 中的名稱開啟試算表並讀取第一個分頁
    return client.open(config.get('google_sheet_name', '活動報到名單')).get_worksheet(0)

def async_update_sheet(updates):
    """背景非同步寫入函式：大幅提高回傳效率的關鍵"""
    try:
        sheet = get_worksheet()
        sheet.batch_update(updates)
        print(f"[{datetime.utcnow() + timedelta(hours=8)}] 背景寫入 Google Sheet 成功")
    except Exception as e:
        print(f"背景寫入失敗: {e}")

def refresh_cache(force=False):
    """更新記憶體快取，包含處理合併儲存格與向下填充邏輯"""
    global participants_cache, last_cache_update
    if not force and (time.time() - last_cache_update < CACHE_TTL) and participants_cache:
        return
    with cache_lock:
        try:
            sheet = get_worksheet()
            all_values = sheet.get_all_values()
            if not all_values: return
            
            config = load_config()
            cols = config.get('excel_columns', {})
            new_cache = []
            last_company = ""
            
            # 從第 4 行開始讀取 (索引 3)，跳過標題
            for i, row in enumerate(all_values[3:]):
                def g(col_idx):
                    return row[col_idx-1].strip() if col_idx-1 < len(row) else ""
                
                # 處理合併的公司名稱欄位（向下填充）
                comp = g(cols.get('company', 3))
                if comp: last_company = comp
                
                name = g(cols.get('name', 6))
                if not name: continue
                
                new_cache.append({
                    "id": name, # 以姓名為唯一標識
                    "name": name,
                    "phone": g(cols.get('phone', 8)),
                    "company": last_company,
                    "email": g(cols.get('email', 9)),
                    "status": g(cols.get('status', 15)),
                    "meal": g(cols.get('meal', 16)),
                    "checkedInAt": g(cols.get('checkedInAt', 14)),
                    "_row": i + 4 # 記住行號，報到時直接定位
                })
            participants_cache = new_cache
            last_cache_update = time.time()
            print(f"快取更新完成，共 {len(new_cache)} 筆資料")
        except Exception as e:
            print(f"同步資料失敗: {e}")

def background_sync():
    """定時背景同步"""
    while True:
        time.sleep(CACHE_TTL)
        refresh_cache(force=True)

# =============================================
# API 路由
# =============================================
@app.route('/')
def index():
    return send_from_directory('.', '活動報到系統.html')

@app.route('/商品頁面.html')
def products_page():
    return send_from_directory('.', '商品頁面.html')

@app.route('/api/config')
def get_config():
    return jsonify(load_config())

@app.route('/api/search/<method>')
def search(method):
    """通用搜尋 API"""
    refresh_cache()
    q = request.args.get(method, "").replace(" ", "")
    if method == 'phone': 
        q = ''.join(filter(str.isdigit, q))
        return jsonify({"success": True, "data": [p for p in participants_cache if ''.join(filter(str.isdigit, p["phone"])) == q]})
    return jsonify({"success": True, "data": [p for p in participants_cache if q.lower() in p.get(method, "").lower()]})

@app.route('/api/checkin/<pid>', methods=['POST'])
def checkin(pid):
    """個人報到：秒級回傳，時區修正，背景寫入"""
    data = request.json
    # 強制設定為台灣時間 UTC+8
    now_tw = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M:%S')
    
    p = next((x for x in participants_cache if x['id'] == pid), None)
    if not p: return jsonify({"success": False, "error": "找不到參加者"}), 404

    meal = data.get('meal', '未選擇')
    
    # 1. 立即更新本地記憶體快取
    p['status'] = 'checked_in'
    p['meal'] = meal
    p['checkedInAt'] = now_tw
    
    # 2. 準備背景寫入指令 (精確對應 N, O, P 欄位)
    cols = load_config().get('excel_columns', {})
    updates = [
        {'range': gspread.utils.rowcol_to_a1(p['_row'], cols['checkedInAt']), 'values': [[now_tw]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], cols['status']), 'values': [['checked_in']]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], cols['meal']), 'values': [[meal]]}
    ]
    # 使用 Thread 將寫入動作丟到背景執行
    threading.Thread(target=async_update_sheet, args=(updates,)).start()
    
    return jsonify({"success": True, "data": p})

@app.route('/api/checkin/batch', methods=['POST'])
def batch_checkin():
    """批次報到：支援多人同時處理"""
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
            # 加入批次更新清單
            all_updates.extend([
                {'range': gspread.utils.rowcol_to_a1(p['_row'], cols['checkedInAt']), 'values': [[now_tw]]},
                {'range': gspread.utils.rowcol_to_a1(p['_row'], cols['status']), 'values': [['checked_in']]},
                {'range': gspread.utils.rowcol_to_a1(p['_row'], cols['meal']), 'values': [[meal]]}
            ])
    
    if all_updates:
        threading.Thread(target=async_update_sheet, args=(all_updates,)).start()
    
    return jsonify({"success": True, "data": results})

if __name__ == '__main__':
    print("活動報到系統啟動中...")
    refresh_cache(force=True)
    threading.Thread(target=background_sync, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
