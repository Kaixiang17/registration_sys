import os, json, time, threading, requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = "rcsa_ark_secure_key_20260508" 
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RENDER_KEY = "/etc/secrets/google-creds.json"
LOCAL_KEY = os.path.join(BASE_DIR, 'test0417-493608-ec0a369af886.json')

participants_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
CACHE_TTL = 300

config_cache = None
last_config_update = 0
CONFIG_TTL = 600

def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_path = RENDER_KEY if os.path.exists(RENDER_KEY) else LOCAL_KEY
    if not os.path.exists(json_path): json_path = os.path.join(BASE_DIR, 'test0417-493608-ec0a369af886.json')
    return gspread.authorize(Credentials.from_service_account_file(json_path, scopes=scope))

def get_worksheet(name=None):
    spreadsheet = get_gspread_client().open("活動報到名單")
    if name:
        try: return spreadsheet.worksheet(name)
        except: return None
    return spreadsheet.get_worksheet(0)

def upload_image_to_free_pool(base64_str):
    if not base64_str or not str(base64_str).startswith("data:image/"):
        return base64_str
    try:
        if "," in base64_str:
            base64_data = base64_str.split(",")[1]
        else:
            base64_data = base64_str
        url = "https://api.imgbb.com/1/upload"
        payload = {
            "key": "2b3149867c4b69cdbda90ea8fbd52ec3",
            "image": base64_data
        }
        res = requests.post(url, data=payload, timeout=15).json()
        if res.get("success"):
            return res["data"]["url"]
    except Exception as e:
        print(f"❌ [圖床失敗]: {e}")
    return base64_str

def load_config_from_sheets(force_refresh=False):
    global config_cache, last_config_update
    if not force_refresh and config_cache and (time.time() - last_config_update < CONFIG_TTL):
        return config_cache

    config_data = {
        "show_meal_options": True, "google_sheet_name": "活動報到名單", "map_image_url": "", "products": [],
        "excel_columns": {"id": 6, "name": 6, "phone": 8, "company": 3, "email": 9, "qrCode": 4, "registeredAt": 5, "checkedInAt": 14, "status": 15, "meal": 16}
    }
    try:
        ws_cfg = get_worksheet("系統設定")
        if ws_cfg:
            vals = ws_cfg.get_all_records()
            if vals:
                row = vals[0]
                config_data["show_meal_options"] = str(row.get("顯示餐點選項", "TRUE")).upper() == "TRUE"
                config_data["map_image_url"] = row.get("地圖圖片網址", "")
                config_data["google_sheet_name"] = row.get("Google試算表名稱", "活動報到名單")
            vals = ws_cfg.get_all_values()
            if len(vals) > 1:
                # 欄位一律鎖死：第一列是標題，第二列是實際值
                # A2 = 顯示餐點選項, B2 = 地圖圖片網址, C2 = Google試算表名稱
                row = vals[1]
                config_data["show_meal_options"] = str(row[0]).upper() == "TRUE" if len(row) > 0 else True
                config_data["map_image_url"] = str(row[1]) if len(row) > 1 else ""
                config_data["google_sheet_name"] = str(row[2]) if len(row) > 2 else "活動報到名單"

        ws_prod = get_worksheet("商品清單")
        if ws_prod:
@@ -94,50 +96,50 @@
        if not config_cache: config_cache = config_data
    return config_cache

# ==================== 【⚡ 核心升級：背景超高速平行處理排程】 ====================
def async_save_process(payload, current_data):
    """ 在背景悄悄執行的苦力活：上傳圖床、同步 Google Sheet，完全不卡網頁速度 """
    print("🛰️ [背景同步] 啟動平行處理排程...")
    """ 背景處理：強制在 Google Sheet 建立指定格子存放地圖，絕對不卡死 """
    print("🛰️ [背景同步] 正在將地圖與商品寫入指定 Google Sheet 格子...")
    try:
        # 1. 處理地圖圖片轉網址
        # 1. 處理圖片轉網址
        if payload.get("map_image_url"):
            payload["map_image_url"] = upload_image_to_free_pool(payload["map_image_url"])
            
        # 2. 處理商品圖片轉網址
        if payload.get("products"):
            for p in payload["products"]:
                if p.get("image"):
                    p["image"] = upload_image_to_free_pool(p["image"])

        # 3. 寫入 Google Sheet 「系統設定」
        # 2. 【核心修正】強制覆寫「系統設定」分頁，確保 A2, B2, C2 位置絕對精準
        ws_cfg = get_worksheet("系統設定")
        if ws_cfg:
            ws_cfg.clear()
            ws_cfg.append_row(["顯示餐點選項", "地圖圖片網址", "Google試算表名稱"])
            ws_cfg.append_row([
                "TRUE" if payload.get("show_meal_options", True) else "FALSE",
                payload.get("map_image_url", ""),
                payload.get("google_sheet_name", "活動報到名單")
            ws_cfg.update('A1:C2', [
                ["顯示餐點選項", "地圖圖片網址", "Google試算表名稱"],
                [
                    "TRUE" if payload.get("show_meal_options", True) else "FALSE",
                    payload.get("map_image_url", ""),
                    payload.get("google_sheet_name", "活動報到名單")
                ]
            ])

        # 4. 寫入 Google Sheet 「商品清單」
        # 3. 覆寫寫入「商品清單」分頁
        ws_prod = get_worksheet("商品清單")
        if ws_prod and "products" in payload:
            ws_prod.clear()
            ws_prod.append_row(["商品名稱", "商品圖片", "商品分類", "商品描述", "購買連結", "是否為贈品"])
            rows_to_write = [["商品名稱", "商品圖片", "商品分類", "商品描述", "購買連結", "是否為贈品"]]
            for p in payload["products"]:
                ws_prod.append_row([
                rows_to_write.append([
                    p.get("name", ""), p.get("image", ""), p.get("category", "課程"),
                    p.get("description", ""), p.get("link", ""), "TRUE" if p.get("isGift") else "FALSE"
                ])
            ws_prod.update(f'A1:F{len(rows_to_write)}', rows_to_write)

        # 5. 更新本地記憶體快取
        # 4. 更新記憶體快取
        global config_cache, last_config_update
        config_cache = payload
        last_config_update = time.time()
        print("🟢 [背景同步] 全數資料已完美且安全地寫入 Google 試算表雲端！")
        print("🟢 [背景同步] 雲端試算表格子已完全對齊，地圖網址成功寫入 B2 格子！")
    except Exception as e:
        print(f"❌ [背景同步嚴重失敗]: {e}")
        print(f"❌ [背景同步失敗]: {e}")

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
@@ -151,19 +153,15 @@
        if "products" not in payload or not payload["products"]:
            payload["products"] = current_data.get("products", [])

        # 保底防呆：如果前端完全沒傳地圖欄位，自動從舊資料抓回來，絕對不讓欄位消失
        if "map_image_url" not in payload:
            payload["map_image_url"] = current_data.get("map_image_url", "")

        # 🔥【秒級回應核心】🔥 不等 Google Sheet 了！直接把工作丟給背景執行緒，立刻和前端說 OK！
        # 拋給背景執行緒，讓前端 0.1 秒內立刻收到回應跳出「儲存成功」
        threading.Thread(target=async_save_process, args=(payload, current_data)).start()
        
        # 0.1秒秒回前端，網頁就會立刻跳出「儲存成功」的提示！
        return jsonify({"success": True, "message": "儲存指令已送出，後台正在非同步同步雲端中...", "data": payload})
        return jsonify({"success": True, "message": "儲存成功！系統正在背景寫入 Google Sheet。"})

    return jsonify(load_config_from_sheets())

# 其餘 API 保持不變 (admin_login, search, checkin, refresh_cache, auto_check_and_patch_sheets 等)
@app.route('/api/login', methods=['POST'])
def admin_login():
    data = request.json
@@ -300,17 +298,16 @@
        try: spreadsheet.worksheet("系統設定")
        except:
            ws = spreadsheet.add_worksheet(title="系統設定", rows="10", cols="5")
            ws.append_row(["顯示餐點選項", "地圖圖片網址", "Google試算表名稱"])
            ws.append_row(["TRUE", "", "活動報到名單"])
            ws.update('A1:C2', [["顯示餐點選項", "地圖圖片網址", "Google試算表名稱"], ["TRUE", "", "活動報到名單"]])
        try: spreadsheet.worksheet("商品清單")
        except:
            ws = spreadsheet.add_worksheet(title="商品清單", rows="50", cols="10")
            ws.append_row(["商品名稱", "商品圖片", "商品分類", "商品描述", "購買連結", "是否為贈品"])
    except Exception as e:
        print(f"❌ [初始化失敗]: {e}")

auto_check_and_patch_sheets()
load_config_from_sheets(force_refresh=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
