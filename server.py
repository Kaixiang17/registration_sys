
在任務完成時開啟瀏覽器通知。


開始免費試用

分享
Lite
您好，我已根據您提供的 server.py 程式碼，進行了最小幅度的修改，以解決地圖檔案在前端無法顯示的問題。修改後的檔案已儲存為 server_fixed.py，請您下載並替換原有的 server.py 檔案。
修改說明：
主要修改集中在 handle_config() 函數中，位於程式碼的第 144 行至 163 行。我將 GET 請求（用於讀取設定）與 POST 請求（用於儲存設定）的權限檢查分開處理：
Python
@app.route("/api/config", methods=["GET", "POST"])
def handle_config():
    # 允許 GET 請求公開訪問，不需登入
    if request.method == "GET":
        return jsonify(load_config_from_sheets())

    # POST 請求（儲存設定）需要管理員登入
    if not session.get("admin_logged_in"):
        return jsonify({"success": False, "message": "未授權的操作"}), 403
            
    # ... (其餘 POST 請求的儲存邏輯保持不變)
修改後的行為：
GET 請求 (/api/config)：現在，任何前端頁面（包括「活動報到系統.html」）都可以直接發送 GET 請求來獲取設定資料，無需管理員登入。這將確保地圖圖片的 URL 能夠被正確讀取並顯示。
POST 請求 (/api/config)：只有已登入的管理員才能發送 POST 請求來儲存或更新設定。這保留了後台管理功能的安全性。
後續步驟：
下載 server_fixed.py：請下載我提供的 server_fixed.py 檔案。
替換原有檔案：將您專案中的 server.py 替換為此 server_fixed.py 檔案。
重新部署：將修改後的專案重新部署到 Render 上。
完成這些步驟後，前端頁面應該就能正常顯示地圖圖片了。如果您還有其他問題，請隨時提出。
server_fixed.py
程式碼 · 14.60 KB
查看此任務中的所有檔案
任務已完成
這個結果怎麼樣？
建議追問
import os, json, time, threading, requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials






server_fixed.py
import os, json, time, threading, requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = "rcsa_ark_secure_key_20260508"
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RENDER_KEY = "/etc/secrets/google-creds.json"
LOCAL_KEY = os.path.join(BASE_DIR, "test0417-493608-ec0a369af886.json")

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
    if not os.path.exists(json_path): json_path = os.path.join(BASE_DIR, "test0417-493608-ec0a369af886.json")
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
            prod_rows = ws_prod.get_all_records()
            for r in prod_rows:
                if not r.get("商品名稱"): continue
                config_data["products"].append({
                    "name": str(r.get("商品名稱")),
                    "image": str(r.get("商品圖片", "")),
                    "category": str(r.get("商品分類", "課程")),
                    "description": str(r.get("商品描述", "")),
                    "link": str(r.get("購買連結", "")),
                    "isGift": str(r.get("是否為贈品", "FALSE")).upper() == "TRUE"
                })
        config_cache = config_data
        last_config_update = time.time()
    except Exception as e:
        print(f"⚠️ [讀取失敗] 沿用舊快取: {e}")
        if not config_cache: config_cache = config_data
    return config_cache

def async_save_process(payload, current_data):
    """ 背景處理：強制在 Google Sheet 建立指定格子存放地圖，絕對不卡死 """
    print("🛰️ [背景同步] 正在將地圖與商品寫入指定 Google Sheet 格子...")
    try:
        # 1. 處理圖片轉網址
        if payload.get("map_image_url"):
            payload["map_image_url"] = upload_image_to_free_pool(payload["map_image_url"])
        if payload.get("products"):
            for p in payload["products"]:
                if p.get("image"):
                    p["image"] = upload_image_to_free_pool(p["image"])
        
        # 2. 【核心修正】強制覆寫「系統設定」分頁，確保 A2, B2, C2 位置絕對精準
        ws_cfg = get_worksheet("系統設定")
        if ws_cfg:
            ws_cfg.clear()
            ws_cfg.update("A1:C2", [
                ["顯示餐點選項", "地圖圖片網址", "Google試算表名稱"],
                [
                    "TRUE" if payload.get("show_meal_options", True) else "FALSE",
                    payload.get("map_image_url", ""),
                    payload.get("google_sheet_name", "活動報到名單")
                ]
            ])
            
        # 3. 覆寫寫入「商品清單」分頁
        ws_prod = get_worksheet("商品清單")
        if ws_prod and "products" in payload:
            ws_prod.clear()
            rows_to_write = [["商品名稱", "商品圖片", "商品分類", "商品描述", "購買連結", "是否為贈品"]]
            for p in payload["products"]:
                rows_to_write.append([
                    p.get("name", ""), p.get("image", ""), p.get("category", "課程"),
                    p.get("description", ""), p.get("link", ""), "TRUE" if p.get("isGift") else "FALSE"
                ])
            ws_prod.update(f"A1:F{len(rows_to_write)}", rows_to_write)
        
        # 4. 更新記憶體快取
        global config_cache, last_config_update
        config_cache = payload
        last_config_update = time.time()
        print("🟢 [背景同步] 雲端試算表格子已完全對齊，地圖網址成功寫入 B2 格子！")
    except Exception as e:
        print(f"❌ [背景同步失敗]: {e}")

@app.route("/api/config", methods=["GET", "POST"])
def handle_config():
    # 允許 GET 請求公開訪問，不需登入
    if request.method == "GET":
        return jsonify(load_config_from_sheets())

    # POST 請求（儲存設定）需要管理員登入
    if not session.get("admin_logged_in"):
        return jsonify({"success": False, "message": "未授權的操作"}), 403
            
    payload = request.json
    current_data = load_config_from_sheets()
    
    if "products" not in payload or not payload["products"]:
        payload["products"] = current_data.get("products", [])
        
    if "map_image_url" not in payload:
        payload["map_image_url"] = current_data.get("map_image_url", "")

    # 拋給背景執行緒，讓前端 0.1 秒內立刻收到回應跳出「儲存成功」
    threading.Thread(target=async_save_process, args=(payload, current_data)).start()
    return jsonify({"success": True, "message": "儲存成功！系統正在背景寫入 Google Sheet。"})
            
@app.route("/api/login", methods=["POST"])
def admin_login():
    data = request.json
    u, p = data.get("username"), data.get("password")
    ws = get_worksheet("管理員")
    if not ws: return jsonify({"success": False, "message": "尚未建立管理員分頁"}), 500
    try:
        sheet_username = ws.cell(2, 1).value
        sheet_password = ws.cell(2, 3).value
        if str(u) == str(sheet_username) and str(p) == str(sheet_password):
            session["admin_logged_in"] = True
            return jsonify({"success": True})
    except Exception as e: return jsonify({"success": False, "message": f"讀取失敗: {e}"}), 500
    return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

@app.route("/api/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect("/login.html")

@app.route("/admin")
def admin_page():
    if not session.get("admin_logged_in"): return send_from_directory(".", "login.html")
    return send_from_directory(".", "admin.html")

@app.route("/api/dashboard_stats")
def get_dashboard_stats():
    if not session.get("admin_logged_in"): return jsonify({"success": False}), 403
    refresh_cache()
    total = len(participants_cache)
    checked_in_list = [p for p in participants_cache if p["status"] in ["checked_in", "已報到", "替代"]]
    logs = [{"name": f"{p["name"]} (替代)" if p["status"] == "替代" else p["name"], "time": p["checkedInAt"], "company": p["company"], "meal": p["meal"]} for p in checked_in_list]
    logs.sort(key=lambda x: x["time"], reverse=True)
    
    stats_data = {}
    for p in participants_cache:
        t = p.get("table", "").strip()
        if not t: continue
        if t not in stats_data: stats_data[t] = {"total": 0, "checked": 0}
        stats_data[t]["total"] += 1
        if p["status"] in ["checked_in", "已報到", "替代"]: stats_data[t]["checked"] += 1
    
    table_stats = {t: round(s["checked"]/s["total"]*100, 1) for t, s in stats_data.items() if s["total"] > 0}

    return jsonify({
        "success": True,
        "stats": { "total": total, "checked_in": len(checked_in_list), "not_checked_in": total - len(checked_in_list), "logs": logs[:25], "table_stats": table_stats }
    })

@app.route("/")
def index(): return send_from_directory(".", "活動報到系統.html")

@app.route("/api/search/<method>")
def search(method):
    refresh_cache()
    q = request.args.get(method, "").strip().lower()
    if method == "company":
        matched_companies = sorted(list(set(p.get("company", "") for p in participants_cache if q in p.get("company", "").lower() and p.get("company"))))
        return jsonify({"success": True, "data": matched_companies})
    if method == "company_members":
        company_name = request.args.get("name", "").strip().lower()
        members = [p for p in participants_cache if p.get("company", "").lower() == company_name]
        return jsonify({"success": True, "data": members})
    return jsonify({"success": True, "data": [p for p in participants_cache if q in p.get(method, "").lower() or q in p.get("name", "").lower()]})

@app.route("/api/checkin/<pid>", methods=["POST"])
def checkin(pid):
    data = request.json
    now_tw = (datetime.utcnow() + timedelta(hours=8)).strftime("%Y/%m/%d %H:%M:%S")
    p = next((x for x in participants_cache if x["id"] == pid), None)
    if not p: return jsonify({"success": False}), 404
    if p["status"] in ["checked_in", "已報到", "替代"]:
        return jsonify({"success": False, "error": "already_done", "data": p})
    
    meal = data.get("meal", "未選擇")
    is_original = data.get("is_original", True)
    proxy_info = data.get("proxy_info", {})
    
    config_tmp = load_config_from_sheets()
    cols = config_tmp.get("excel_columns", {})
    status_val = "checked_in" if is_original else "替代"
    
    updates = [
        {"range": gspread.utils.rowcol_to_a1(p["_row"], int(cols.get("checkedInAt", 14))), "values": [[now_tw]]},
        {"range": gspread.utils.rowcol_to_a1(p["_row"], int(cols.get("status", 15))), "values": [[status_val]]},
        {"range": gspread.utils.rowcol_to_a1(p["_row"], int(cols.get("meal", 16))), "values": [[meal]]}
    ]
    p_name_col, p_phone_col, p_email_col = 17, 18, 19
    if not is_original and proxy_info:
        updates.append({"range": gspread.utils.rowcol_to_a1(p["_row"], p_name_col), "values": [[proxy_info.get("name", "")]]})
        updates.append({"range": gspread.utils.rowcol_to_a1(p["_row"], p_phone_col), "values": [[proxy_info.get("phone", "")]]})
        updates.append({"range": gspread.utils.rowcol_to_a1(p["_row"], p_email_col), "values": [[proxy_info.get("email", "")]]})
    else:
        updates.extend([{"range": gspread.utils.rowcol_to_a1(p["_row"], c), "values": [[""]]} for c in [p_name_col, p_phone_col, p_email_col]])
            
    threading.Thread(target=async_update_sheet, args=(updates,)).start()
    p.update({"status": status_val, "meal": meal, "checkedInAt": now_tw})
    return jsonify({"success": True, "data": p})

def async_update_sheet(updates):
    try: get_worksheet().batch_update(updates)
    except Exception as e: print(f"背景同步失敗: {e}")

def refresh_cache(force=False):
    global participants_cache, last_cache_update
    if not force and (time.time() - last_cache_update < CACHE_TTL) and participants_cache: return
    with cache_lock:
        try:
            all_values = get_worksheet().get_all_values()
            config_tmp = load_config_from_sheets()
            cols = config_tmp.get("excel_columns", {})
            new_cache = []
            last_company = ""
            for i, row in enumerate(all_values[3:]):
                def g(c): return row[c-1].strip() if c and c-1 < len(row) else ""
                current_comp = g(cols.get("company", 3))
                if current_comp: last_company = current_comp
                name = g(cols.get("name", 6))
                if not name: continue
                new_cache.append({
                    "id": f"{name}_{i}", "name": name, "phone": g(cols.get("phone", 8)), "company": last_company,
                    "email": g(cols.get("email", 9)), "status": g(cols.get("status", 15)), "meal": g(cols.get("meal", 16)),
                    "checkedInAt": g(cols.get("checkedInAt", 14)), "seat": g(cols.get("seat", 13)), 
                    "table": g(cols.get("seat", 13))[:2] if g(cols.get("seat", 13))[:2].isdigit() else "", "_row": i + 4 
                })
            participants_cache = new_cache
            last_cache_update = time.time()
        except Exception as e: print(f"緩存刷新失敗: {e}")

def auto_check_and_patch_sheets():
    try:
        client = get_gspread_client()
        spreadsheet = client.open("活動報到名單")
        try: spreadsheet.worksheet("系統設定")
        except:
            ws = spreadsheet.add_worksheet(title="系統設定", rows="10", cols="5")
            ws.update("A1:C2", [["顯示餐點選項", "地圖圖片網址", "Google試算表名稱"], ["TRUE", "", "活動報到名單"]])
        try: spreadsheet.worksheet("商品清單")
        except:
            ws = spreadsheet.add_worksheet(title="商品清單", rows="50", cols="10")
            ws.append_row(["商品名稱", "商品圖片", "商品分類", "商品描述", "購買連結", "是否為贈品"])
    except Exception as e:
        print(f"❌ [初始化失敗]: {e}")

auto_check_and_patch_sheets()
load_config_from_sheets(force_refresh=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
Why Does This GitHub Page Fail to Display Images on Mobile? - Manus
