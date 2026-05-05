# server.py 核心修正部分
@app.route('/api/checkin/<pid>', methods=['POST'])
def checkin(pid):
    data = request.json
    now_tw = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M:%S')
    p = next((x for x in participants_cache if x['id'] == pid), None)
    if not p: return jsonify({"success": False}), 404
    
    # 防止重複報到
    if p['status'] in ['checked_in', '已報到', '替代']:
        return jsonify({"success": False, "error": "already_done", "data": p})
    
    meal = data.get('meal', '未選擇')
    is_original = data.get('is_original', True)
    proxy_info = data.get('proxy_info', {}) # 抓取前端送來的替代者物件
    
    cols = load_config().get('excel_columns', {})
    # 根據是否為本人決定狀態字樣[cite: 10]
    status_val = 'checked_in' if is_original else '替代'
    
    # 取得替代者欄位索引 (Q=17, R=18, S=19)
    p_name_col = cols.get('proxyName', 17)
    p_phone_col = cols.get('proxyPhone', 18)
    p_email_col = cols.get('proxyEmail', 19)
    
    updates = [
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('checkedInAt', 14))), 'values': [[now_tw]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('status', 15))), 'values': [[status_val]]},
        {'range': gspread.utils.rowcol_to_a1(p['_row'], int(cols.get('meal', 16))), 'values': [[meal]]}
    ]
    
    # 如果是替代報到，寫入詳細資訊；如果是本人報到，則清空該區域[cite: 10]
    if not is_original and proxy_info:
        updates.append({'range': gspread.utils.rowcol_to_a1(p['_row'], int(p_name_col)), 'values': [[proxy_info.get('name', '')]]})
        updates.append({'range': gspread.utils.rowcol_to_a1(p['_row'], int(p_phone_col)), 'values': [[proxy_info.get('phone', '')]]})
        updates.append({'range': gspread.utils.rowcol_to_a1(p['_row'], int(p_email_col)), 'values': [[proxy_info.get('email', '')]]})
    else:
        # 強制清空，防止測試資料殘留
        updates.append({'range': gspread.utils.rowcol_to_a1(p['_row'], int(p_name_col)), 'values': [['']]})
        updates.append({'range': gspread.utils.rowcol_to_a1(p['_row'], int(p_phone_col)), 'values': [['']]})
        updates.append({'range': gspread.utils.rowcol_to_a1(p['_row'], int(p_email_col)), 'values': [['']]})
            
    threading.Thread(target=async_update_sheet, args=(updates,)).start()
    p.update({"status": status_val, "meal": meal, "checkedInAt": now_tw})
    return jsonify({"success": True, "data": p})
