#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
registration_sys C 方案快速修正器
使用方式：放在 registration_sys 專案根目錄，執行：python apply_registration_sys_C_fix.py
會備份被修改的檔案為 *.bak_C_fix
"""
from pathlib import Path
import re
import sys
from datetime import datetime

ROOT = Path.cwd()
STAMP = datetime.now().strftime('%Y%m%d_%H%M%S')
FILES = {
    'admin': ROOT / 'admin.html',
    'dashboard': ROOT / 'dashboard.html',
    'theme': ROOT / 'smart-ark-theme.css',
    'front': ROOT / '活動報到系統.html',
    'server': ROOT / 'server.py',
}

C_BG = '#061A18'
C_PRIMARY = '#14B8A6'
C_ACCENT = '#5EEAD4'
C_TEXT = '#ECFEFF'

def read(p: Path) -> str:
    if not p.exists():
        print(f'⚠️ 找不到 {p.name}，略過')
        return ''
    return p.read_text(encoding='utf-8')

def write(p: Path, s: str):
    if not p.exists():
        return
    bak = p.with_suffix(p.suffix + f'.bak_C_fix_{STAMP}')
    if not bak.exists():
        bak.write_text(p.read_text(encoding='utf-8'), encoding='utf-8')
    p.write_text(s, encoding='utf-8')
    print(f'✅ 已修改 {p.name}（備份：{bak.name}）')

def rep(s, old, new):
    if old in s:
        return s.replace(old, new)
    return s

def replace_between(s, start_pat, end_pat, replacement, flags=re.S):
    pat = re.compile(start_pat + r'.*?' + end_pat, flags)
    if pat.search(s):
        return pat.sub(replacement, s, count=1)
    print(f'⚠️ 找不到區塊：{start_pat[:50]} ... {end_pat[:50]}')
    return s

def inject_before(s, marker, block):
    if block.strip() in s:
        return s
    if marker in s:
        return s.replace(marker, block + '\n\n' + marker, 1)
    print(f'⚠️ 找不到插入點：{marker[:60]}')
    return s

def inject_after(s, marker, block):
    if block.strip() in s:
        return s
    if marker in s:
        return s.replace(marker, marker + '\n' + block, 1)
    print(f'⚠️ 找不到插入點：{marker[:60]}')
    return s

def apply_c_palette(s: str) -> str:
    # 只處理視覺檔，保留紅色錯誤狀態，不碰後端程式。
    pairs = [
        ('#020817', C_BG), ('#020c1b', C_BG), ('#020C1B', C_BG), ('#06111f', C_BG), ('#061425', C_BG),
        ('#00e5ff', C_PRIMARY), ('#00E5FF', C_PRIMARY), ('#00f0ff', C_ACCENT),
        ('rgba(0,229,255', 'rgba(20,184,166'), ('rgba(0, 229, 255', 'rgba(20, 184, 166'),
        ('#4ade80', C_ACCENT), ('#00e676', C_ACCENT), ('#00E676', C_ACCENT),
        ('rgba(74,222,128', 'rgba(94,234,212'), ('rgba(74, 222, 128', 'rgba(94, 234, 212'),
        ('#ddeeff', C_TEXT), ('#eaffff', C_TEXT), ('#f4ffff', C_TEXT), ('#f2fbff', C_TEXT),
    ]
    for old, new in pairs:
        s = s.replace(old, new)
    return s

# ------------------------------------------------------------
# smart-ark-theme.css
# ------------------------------------------------------------
css = read(FILES['theme'])
if css:
    css = rep(css, '--text-dark: #14B8A6;', f'--theme-bg: {C_BG};\n    --theme-primary: {C_PRIMARY};\n    --theme-accent: {C_ACCENT};\n    --theme-text: {C_TEXT};\n    --text-dark: {C_PRIMARY};')
    css = re.sub(r'--text-glow:\s*rgba\([^;]+;', '    --text-glow: rgba(20, 184, 166, 0.6);', css)
    css = re.sub(r'--accent-gray:\s*rgba\([^;]+;', '    --accent-gray: rgba(94, 234, 212, 0.32);', css)
    css = re.sub(r'body\s*\{\s*font-family: var\(--font-main\);\s*color:\s*#[0-9A-Fa-f]+;', f'body {{\n    font-family: var(--font-main);\n    color: {C_TEXT};', css)
    css = apply_c_palette(css)
    if 'body.circuit-background.is-mobile-view' not in css:
        css += f'''

/* C 方案與裝置偵測補強 */
body.circuit-background {{ background-color: {C_BG} !important; }}
body.is-mobile-view main {{ max-width: 100%; }}
@media (max-width: 768px) {{
  .cyber-card {{ border-radius: 1rem !important; }}
  .verify-grid, .products-grid {{ grid-template-columns: 1fr !important; }}
}}
'''
    write(FILES['theme'], css)

# ------------------------------------------------------------
# admin.html
# ------------------------------------------------------------
admin = read(FILES['admin'])
if admin:
    admin = apply_c_palette(admin)
    replacements = {
        '🚀 活動 Navigator': '🚀 智匯方舟',
        '活動 Navigator / 產業大會視覺設定': '智匯方舟 / 產業大會視覺設定',
        'Navigator 設定': '智匯方舟設定',
        '儲存 Navigator 設定': '儲存智匯方舟設定',
        'Navigator Directory': '產業導覽',
        '智慧論壇 Navigator': '智匯方舟',
        'navigator-config-updated': 'smartark-config-updated',
        '特殊飲食備註 (不吃的)': '肖像權同意狀態',
        '備註內容': '肖像權',
        '尚無備註': '尚無肖像權紀錄',
    }
    for old, new in replacements.items():
        admin = admin.replace(old, new)

    # loadConfig 裡 bannerImageUrl 空值保護，避免後台設定載入失敗
    admin = admin.replace(
        "document.getElementById('bannerImageUrl').value = currentConfig.banner_image_url || '';",
        "const bannerInput = document.getElementById('bannerImageUrl');\n                if (bannerInput) bannerInput.value = currentConfig.banner_image_url || '';"
    )
    # postConfig 帶入目前 admin/sheet，避免儲存到預設場次
    admin = admin.replace(
        "fetch(`${API_BASE}/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(currentConfig) })",
        "fetch(`${API_BASE}/config${getActiveEventQuery()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(currentConfig) })"
    )

    success_ui = '''
                <div class="experience-section-title">六、報到成功頁設定</div>
                <div class="experience-grid">
                    <div class="form-group"><label>成功頁主標題</label><input id="exp_success_main_title" class="cyber-input" placeholder="智匯方舟"></div>
                    <div class="form-group"><label>成功頁副標題</label><input id="exp_success_subtitle" class="cyber-input" placeholder="SMART WISDOM ARK｜世代共榮的數位聚合"></div>
                    <div class="form-group experience-grid-full"><label>成功頁說明文字</label><textarea id="exp_success_description" class="cyber-input" rows="3" placeholder="報到成功，歡迎登艦。"></textarea></div>
                    <div class="form-group"><label>成功頁按鈕文字</label><input id="exp_success_button_text" class="cyber-input" placeholder="查看大會資訊"></div>
                    <div class="form-group"><label>成功頁按鈕 URL</label><input id="exp_success_button_url" class="cyber-input" placeholder="https://..."></div>
                </div>

                <div class="experience-section-title">七、報到成功頁四個資訊卡</div>
                <p style="color:#aab7c4; line-height:1.7; margin-bottom:0.8rem;">順序會依照下方排列顯示；URL 可留空，留空時使用原本地圖、商品、影片、議程功能。</p>
                <div id="successInfoCardEditorList" style="display:grid; gap:0.85rem;"></div>
                <button type="button" class="adm-btn adm-btn-success adm-btn-sm" onclick="addSuccessInfoCardRow()">➕ 新增資訊卡</button>
'''
    admin = inject_before(admin, '                <div class="experience-section-title">六、投影大標題</div>', success_ui)
    admin = admin.replace('                <div class="experience-section-title">六、投影大標題</div>', '                <div class="experience-section-title">八、投影大標題</div>')

    admin_success_js = r'''
        const DEFAULT_SUCCESS_INFO_CARDS = [
            { icon:'🕘', title:'大會時空座標', subtitle:'實體進化航線預載', description:'09:30 - 17:00 航程時間軸', action:'agenda', enabled:true },
            { icon:'🎁', title:'活動商品專區', subtitle:'精選伴手禮與補給品', description:'點擊進入物資艙查看', action:'gift', enabled:true },
            { icon:'▶', title:'核心引擎啟動', subtitle:'智能全新數位中控系統', description:'三年經營現況影片', action:'video', enabled:true },
            { icon:'🧭', title:'2026 產業星圖', subtitle:'領航員名冊與每攤機會', description:'產業導覽', action:'map', enabled:true }
        ];

        function parseAdminConfigJson(value, fallback) {
            if (Array.isArray(value) || (value && typeof value === 'object')) return value;
            if (typeof value === 'string' && value.trim()) {
                try { return JSON.parse(value); } catch(e) {}
            }
            return fallback;
        }

        function writeSuccessCardConfig(cfg = {}) {
            const parsed = parseAdminConfigJson(cfg, {}) || {};
            const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ''; };
            set('exp_success_main_title', parsed.title || parsed.main_title || '智匯方舟');
            set('exp_success_subtitle', parsed.subtitle || 'SMART WISDOM ARK｜世代共榮的數位聚合');
            set('exp_success_description', parsed.description || '報到成功，歡迎登艦。');
            set('exp_success_button_text', parsed.button_text || parsed.buttonText || '');
            set('exp_success_button_url', parsed.button_url || parsed.buttonUrl || '');
        }

        function collectSuccessCardConfig() {
            const val = id => (document.getElementById(id)?.value || '').trim();
            return {
                title: val('exp_success_main_title'),
                subtitle: val('exp_success_subtitle'),
                description: val('exp_success_description'),
                button_text: val('exp_success_button_text'),
                button_url: val('exp_success_button_url')
            };
        }

        function successInfoCardRowTemplate(item = {}) {
            const action = item.action || item.type || '';
            const enabled = item.enabled === false ? '' : 'checked';
            return `<div class="success-info-card-row" style="display:grid; grid-template-columns:72px 1fr 1fr 1.3fr 1fr 110px 52px; gap:0.6rem; align-items:center; border:1px solid rgba(20,184,166,.18); border-radius:0.85rem; padding:0.75rem; background:rgba(20,184,166,.04);">
                <input class="cyber-input suc-card-icon" style="margin:0;" placeholder="圖示" value="${escapeHtml(item.icon || '')}">
                <input class="cyber-input suc-card-title" style="margin:0;" placeholder="標題" value="${escapeHtml(item.title || '')}">
                <input class="cyber-input suc-card-subtitle" style="margin:0;" placeholder="標籤/副標" value="${escapeHtml(item.subtitle || item.tag || '')}">
                <input class="cyber-input suc-card-desc" style="margin:0;" placeholder="文字敘述" value="${escapeHtml(item.description || '')}">
                <input class="cyber-input suc-card-url" style="margin:0;" placeholder="URL，可留空" value="${escapeHtml(item.url || '')}">
                <select class="cyber-input suc-card-action" style="margin:0; padding:0.7rem;">
                    ${['agenda','gift','video','map','url'].map(x => `<option value="${x}" ${action === x ? 'selected' : ''}>${x}</option>`).join('')}
                </select>
                <label class="experience-check" style="margin:0;"><input type="checkbox" class="suc-card-enabled" ${enabled}></label>
                <button class="adm-btn adm-btn-danger adm-btn-sm" style="grid-column:1 / -1; justify-self:end;" onclick="this.closest('.success-info-card-row').remove()">刪除</button>
            </div>`;
        }

        function renderSuccessInfoCardsEditor(cards) {
            const box = document.getElementById('successInfoCardEditorList');
            if (!box) return;
            const list = parseAdminConfigJson(cards, DEFAULT_SUCCESS_INFO_CARDS) || DEFAULT_SUCCESS_INFO_CARDS;
            box.innerHTML = list.map(successInfoCardRowTemplate).join('');
        }

        function addSuccessInfoCardRow(item = {}) {
            const box = document.getElementById('successInfoCardEditorList');
            if (box) box.insertAdjacentHTML('beforeend', successInfoCardRowTemplate(item));
        }

        function collectSuccessInfoCards() {
            return [...document.querySelectorAll('.success-info-card-row')].map(row => ({
                icon: row.querySelector('.suc-card-icon')?.value.trim() || '•',
                title: row.querySelector('.suc-card-title')?.value.trim() || '',
                subtitle: row.querySelector('.suc-card-subtitle')?.value.trim() || '',
                description: row.querySelector('.suc-card-desc')?.value.trim() || '',
                url: row.querySelector('.suc-card-url')?.value.trim() || '',
                action: row.querySelector('.suc-card-action')?.value || 'url',
                enabled: !!row.querySelector('.suc-card-enabled')?.checked
            })).filter(x => x.title || x.description || x.url);
        }
'''
    admin = inject_after(admin, "        function writeExpField(key, value) {\n            const el = expEl(key);\n            if (!el) return;\n            if (el.type === 'checkbox') el.checked = value === undefined || value === null ? true : !!value;\n            else el.value = value || '';\n        }", admin_success_js)

    admin = admin.replace(
        "expKeys.forEach(k => writeExpField(k, cfg[k]));",
        "expKeys.forEach(k => writeExpField(k, cfg[k]));\n                writeSuccessCardConfig(cfg.success_card_config || cfg);\n                renderSuccessInfoCardsEditor(cfg.success_info_cards_config);"
    )
    admin = admin.replace(
        "expKeys.forEach(k => payload[k] = readExpField(k));\n            if (payload.map_image_url) payload.map_image_url = String(payload.map_image_url).trim();",
        "expKeys.forEach(k => payload[k] = readExpField(k));\n            payload.success_card_config = collectSuccessCardConfig();\n            payload.success_info_cards_config = collectSuccessInfoCards();\n            payload.theme_background_color = '#061A18';\n            payload.theme_primary_color = '#14B8A6';\n            payload.theme_accent_color = '#5EEAD4';\n            payload.theme_text_color = '#ECFEFF';\n            if (payload.map_image_url) payload.map_image_url = String(payload.map_image_url).trim();"
    )

    new_render_meal_stats = r'''function renderMealStats(meals = {}, specialNotes = [], logs = []) {
            const counts = normalizeMealCounts(meals, logs);
            document.getElementById('meal-meat').textContent = counts.葷 || 0;
            document.getElementById('meal-vege').textContent = counts.素 || 0;
            document.getElementById('meal-other').textContent = counts.其他 || 0;

            const notesBody = document.getElementById('meal-notes-body');
            const apiRows = Array.isArray(specialNotes) ? specialNotes : [];
            const rows = apiRows.length ? apiRows : (logs || []).map(item => ({
                name: item.name || item.姓名 || '',
                company: item.company || item.company_name || item.公司 || '',
                portrait_consent_status: pickPortraitStatus(item)
            })).filter(x => x.name || x.company || x.portrait_consent_status);

            const normalizePortraitText = (item = {}) => {
                const raw = String(item.portrait_consent_status || item.image_rights_status || item.note || item.notes || pickPortraitStatus(item) || '').trim();
                if (/不同意|拒絕|否|false|0/i.test(raw)) return '不同意肖像權';
                if (/同意|yes|true|1/i.test(raw)) return '同意肖像權';
                return '未填';
            };

            if (rows.length > 0) {
                notesBody.innerHTML = rows.map(n => `
                    <tr>
                        <td style="padding:0.75rem; border-bottom:1px solid rgba(255,255,255,0.05);">${escapeAdminHtml(n.name || '')}</td>
                        <td style="padding:0.75rem; border-bottom:1px solid rgba(255,255,255,0.05);">${escapeAdminHtml(n.company_name || n.company || '')}</td>
                        <td style="padding:0.75rem; border-bottom:1px solid rgba(255,255,255,0.05); color:#5EEAD4;">${escapeAdminHtml(normalizePortraitText(n))}</td>
                    </tr>
                `).join('');
            } else {
                notesBody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:1rem; color:#888;">尚無肖像權紀錄</td></tr>';
            }
        }

        '''
    admin = replace_between(admin, r'function renderMealStats\(meals = \{\}, specialNotes = \[\], logs = \[\]\) \{', r'async function updateDashboard\(\) \{', new_render_meal_stats + 'async function updateDashboard() {')
    admin = admin.replace("${meal ? `餐食：${escapeAdminHtml(meal)}<br>` : ''}", "餐食：${escapeAdminHtml(meal || '未選擇')}<br>")
    write(FILES['admin'], admin)

# ------------------------------------------------------------
# dashboard.html
# ------------------------------------------------------------
dash = read(FILES['dashboard'])
if dash:
    dash = apply_c_palette(dash)
    dash = dash.replace("['agenda-updated', 'navigator-config-updated'].includes(event.data.type)", "['agenda-updated', 'smartark-config-updated', 'navigator-config-updated', 'industry-mapping-updated', 'checkin-success'].includes(event.data.type)")
    dash = dash.replace("if (event.key === 'dashboard_refresh_signal') loadCfg();", "if (['dashboard_refresh_signal','smartArkIndustryMappingUpdated','smartArkCheckinSuccess'].includes(event.key)) { lastCheckCount = -1; loadCfg().then(poll); }")
    dash = dash.replace(
        "cfg = Object.assign({}, baseCfg || {}, eventCfg || {});\n    applyHeader();\n    renderAgenda();\n    renderEnterprises();",
        "let agendaItems = [];\n    try {\n      const agendaJson = await get(`${API}/agenda?admin=${enc(ADMIN)}&sheet=${enc(SHEET)}`);\n      if (agendaJson.success) agendaItems = agendaJson.data || agendaJson.agenda || [];\n    } catch {}\n    cfg = Object.assign({}, baseCfg || {}, eventCfg || {});\n    cfg.agenda = agendaItems;\n    lastCheckCount = -1;\n    applyHeader();\n    renderAgenda();\n    renderEnterprises(true);"
    )
    old_poll = """    // fingerprint = total + checked_in
    const fingerprint = (s.total ?? 0) * 100000 + (s.checked_in ?? 0);

    
    const checked = s.checked_in ?? 0;
    const tot = s.total ?? 0;
    setText('donut-num', checked > 0 ? '100%' : '—');
    setText('donut-lbl', checked > 0 ? '已報到' : '');

    if (fingerprint !== lastCheckCount) {
      lastCheckCount = fingerprint;
      buildDonut(s.logs || [], s.total ?? 0);
    }
"""
    new_poll = """    const checked = s.checked_in ?? 0;
    const tot = s.total ?? 0;
    const checkedRate = tot ? Math.round(checked / tot * 100) : 0;
    setText('donut-num', tot ? `${checkedRate}%` : '—');
    setText('donut-lbl', tot ? '已報到率' : '');

    const industrySource = s.industry_counts || s.checked_industry_stats || s.industry_stats || null;
    const fingerprint = JSON.stringify({ total: tot, checked, industrySource });
    if (fingerprint !== lastCheckCount) {
      lastCheckCount = fingerprint;
      buildDonut(industrySource || s.logs || [], tot);
    }
"""
    dash = dash.replace(old_poll, new_poll)
    new_build_donut = r'''function buildDonut(source, total) {
  const counter  = {};

  if (source && !Array.isArray(source) && typeof source === 'object') {
    for (const [name, count] of Object.entries(source)) {
      const n = Number(count || 0);
      if (n > 0) counter[name || '其他'] = n;
    }
  } else {
    const mappings = cfg?.industry_mappings || [];
    for (const log of (source || [])) {
      const direct = String(log.industry || '').trim();
      const co  = String(log.company || log.company_name || '');
      let cat = direct || '其他';
      if (!direct) {
        for (const m of mappings) {
          const keyword = String(m.keyword || m.company_name || '').trim();
          const category = String(m.category || m.industry || '').trim();
          if (keyword && co.includes(keyword)) { cat = category || '其他'; break; }
        }
      }
      counter[cat] = (counter[cat] || 0) + 1;
    }
  }

  const logTotal = Object.values(counter).reduce((a,b) => a+b, 0);
  const segs = Object.entries(counter)
    .filter(([,count]) => Number(count) > 0)
    .sort((a,b) => b[1]-a[1])
    .map(([name, count], i) => ({
      name, count,
      pct: logTotal ? +(count/logTotal*100).toFixed(1) : 0,
      color: PALETTE[i % PALETTE.length],
    }));

  drawDonut(segs, total);
  drawLegend(segs);
}

'''
    dash = replace_between(dash, r'function buildDonut\(logs, total\) \{', r'function drawDonut\(segs, total\) \{', new_build_donut + 'function drawDonut(segs, total) {')
    write(FILES['dashboard'], dash)

# ------------------------------------------------------------
# front HTML
# ------------------------------------------------------------
front = read(FILES['front'])
if front:
    front = apply_c_palette(front)
    front = front.replace('Navigator Directory', '產業導覽')
    front = front.replace('領航員 ${name}${jobTitle ? \' \' + jobTitle : \'\'}', "${name}${jobTitle ? '｜' + jobTitle : ''}")
    front = front.replace("`您好，領航員 ${name}${jobTitle ? ' ' + jobTitle : ''}`", "`您好，${name}${jobTitle ? '｜' + jobTitle : ''}`")
    front = front.replace("job_title') || '學員'", "job_title') || ''")
    # 公司卡右上角「代」
    badge_css = f'''
        .company-checkin-card {{ position: relative; }}
        .company-checkin-card::after {{
            content: '代';
            position: absolute;
            top: 0.9rem;
            right: 0.9rem;
            width: 34px;
            height: 34px;
            border-radius: 8px;
            display: grid;
            place-items: center;
            background: linear-gradient(135deg, {C_PRIMARY}, {C_ACCENT});
            color: {C_BG};
            font-weight: 900;
            font-size: 1rem;
            box-shadow: 0 0 14px rgba(94,234,212,.32);
            z-index: 2;
        }}
        @media(max-width:768px) {{ .company-checkin-card::after {{ top:0.7rem; right:0.7rem; width:30px; height:30px; }} }}
'''
    front = inject_after(front, "        .verify-card {\n            padding: 2.5rem 2rem;\n            text-align: center;\n            cursor: pointer;\n            border-radius: 1rem;\n            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);\n        }", badge_css)
    front = front.replace("<div class=\"verify-card cyber-card\" onclick=\"prepareSearch('company', '公司')\">", "<div class=\"verify-card cyber-card company-checkin-card\" onclick=\"prepareSearch('company', '公司')\">")
    # 成功卡描述/按鈕
    success_desc_html = '''
                        <div id="arkSuccessDescription" style="color:rgba(236,254,255,.72); font-size:.9rem; line-height:1.7; margin:-.7rem 0 1.2rem; display:none;"></div>
                        <a id="arkSuccessButton" class="ck-btn" target="_blank" rel="noopener" style="display:none; text-decoration:none; margin-bottom:1.2rem; font-size:.9rem; padding:.75rem 1rem;"></a>'''
    front = inject_after(front, '<div class="ark-terminal-subtitle">SMART WISDOM ARK｜世代共榮的數位聚合</div>', success_desc_html)

    # NAVIGATOR_TEMPLATE 補成功頁預設
    front = front.replace(
        "map_description: 'A-MALL.png / 產業導覽'",
        "map_description: '產業導覽',\n            success_main_title: '智匯方舟',\n            success_subtitle: 'SMART WISDOM ARK｜世代共榮的數位聚合',\n            success_description: '報到成功，歡迎登艦。'"
    )

    front_success_js = r'''
        function parseFrontConfigJson(value, fallback) {
            if (Array.isArray(value) || (value && typeof value === 'object')) return value;
            if (typeof value === 'string' && value.trim()) {
                try { return JSON.parse(value); } catch(e) {}
            }
            return fallback;
        }

        function getSuccessCardConfig() {
            const c = systemConfig || {};
            const parsed = parseFrontConfigJson(c.success_card_config, {}) || {};
            return {
                title: parsed.title || parsed.main_title || c.success_main_title || c.brand_name || NAVIGATOR_TEMPLATE.success_main_title,
                subtitle: parsed.subtitle || c.success_subtitle || `SMART WISDOM ARK｜${c.event_subtitle || c.projection_title || NAVIGATOR_TEMPLATE.event_subtitle}`,
                description: parsed.description || c.success_description || NAVIGATOR_TEMPLATE.success_description,
                button_text: parsed.button_text || parsed.buttonText || '',
                button_url: parsed.button_url || parsed.buttonUrl || ''
            };
        }

        function getDefaultSuccessInfoCards() {
            return [
                { icon:'🕘', title: navTpl('flow_title'), subtitle:'實體進化航線預載', description: navTpl('flow_description'), action:'agenda', enabled:true },
                { icon:'🎁', title: navTpl('gift_title'), subtitle:'精選伴手禮與補給品', description: navTpl('gift_description'), action:'gift', enabled:true },
                { icon:'▶', title: navTpl('video_title'), subtitle:'智能全新數位中控系統', description: navTpl('video_description'), action:'video', enabled:true },
                { icon:'🧭', title: navTpl('map_title'), subtitle: navTpl('map_subtitle'), description: navTpl('map_description'), action:'map', enabled:true }
            ];
        }

        function getSuccessInfoCardsConfig() {
            const cards = parseFrontConfigJson(systemConfig?.success_info_cards_config, null);
            const list = Array.isArray(cards) && cards.length ? cards : getDefaultSuccessInfoCards();
            return list.filter(card => card && card.enabled !== false).slice(0, 4);
        }

        function openSuccessInfoCard(index) {
            const card = (window.successInfoCards || [])[index];
            if (!card) return;
            if (card.url) return window.open(card.url, '_blank', 'noopener');
            const action = card.action || card.type || '';
            if (action === 'map') return openArkMap();
            if (action === 'gift') return openArkGift();
            if (action === 'video') return openArkVideo();
            if (action === 'agenda') return openArkFlow();
        }
'''
    front = inject_before(front, "        function applyArkCardsFromConfig() {", front_success_js)

    new_apply_cards = r'''function applyArkCardsFromConfig() {
            const grid = document.querySelector('.ark-terminal-grid');
            if (!grid) return;
            const cards = getSuccessInfoCardsConfig();
            window.successInfoCards = cards;
            grid.innerHTML = cards.map((card, index) => `
                <button type="button" class="ark-nav-card" onclick="openSuccessInfoCard(${index})">
                    <div class="ark-card-icon">${escapeHtml(card.icon || '•')}</div>
                    <div>
                        <div class="ark-card-title">【${escapeHtml(card.title || '')}】</div>
                        <div class="ark-card-sub">${escapeHtml(card.subtitle || card.tag || '')}</div>
                        <div class="ark-card-desc">${escapeHtml(card.description || '')}</div>
                    </div>
                    <div class="ark-card-arrow">›</div>
                </button>
            `).join('');
        }

        '''
    front = replace_between(front, r'function applyArkCardsFromConfig\(\) \{', r'function applyArkBrandFromConfig\(\) \{', new_apply_cards + 'function applyArkBrandFromConfig() {')

    new_apply_brand = r'''function applyArkBrandFromConfig() {
            const c = systemConfig || {};
            const successCfg = getSuccessCardConfig();
            const titleEl = document.querySelector('.ark-terminal-title');
            const subEl = document.querySelector('.ark-terminal-subtitle');
            const descEl = document.getElementById('arkSuccessDescription');
            const btnEl = document.getElementById('arkSuccessButton');
            if (titleEl) titleEl.textContent = successCfg.title || c.brand_name || c.exp_brand_name || NAVIGATOR_TEMPLATE.brand_name;
            if (subEl) subEl.textContent = successCfg.subtitle || `SMART WISDOM ARK｜${c.event_subtitle || c.exp_event_subtitle || c.projection_title || NAVIGATOR_TEMPLATE.event_subtitle}`;
            if (descEl) {
                descEl.textContent = successCfg.description || '';
                descEl.style.display = successCfg.description ? 'block' : 'none';
            }
            if (btnEl) {
                if (successCfg.button_text && successCfg.button_url) {
                    btnEl.textContent = successCfg.button_text;
                    btnEl.href = successCfg.button_url;
                    btnEl.style.display = 'inline-flex';
                } else {
                    btnEl.style.display = 'none';
                    btnEl.removeAttribute('href');
                }
            }
        }

        '''
    front = replace_between(front, r'function applyArkBrandFromConfig\(\) \{', r'function setCardText\(id, value\) \{', new_apply_brand + 'function setCardText(id, value) {')
    write(FILES['front'], front)

# ------------------------------------------------------------
# server.py
# ------------------------------------------------------------
server = read(FILES['server'])
if server:
    # 不動導入，json 已存在
    server = server.replace('"brand_name": "智慧方舟 SMART WISDOM ARK",', '"brand_name": "智匯方舟 SMART WISDOM ARK",')
    # 增補 defaults（若尚未存在）
    if '"success_card_config"' not in server:
        server = server.replace(
            '    "projection_subtitle": "DIGITAL CONVERGENCE FOR GENERATIONAL PROSPERITY"\n}',
            '''    "projection_subtitle": "DIGITAL CONVERGENCE FOR GENERATIONAL PROSPERITY",
    "map_title": "2026 產業星圖",
    "map_subtitle": "領航員名冊與每攤機會",
    "map_image_url": "",
    "map_description": "產業導覽",
    "map_enabled": True,
    "success_card_config": {"title":"智匯方舟","subtitle":"SMART WISDOM ARK｜世代共榮的數位聚合","description":"報到成功，歡迎登艦。","button_text":"","button_url":""},
    "success_info_cards_config": [
        {"icon":"🕘","title":"大會時空座標","subtitle":"實體進化航線預載","description":"09:30 - 17:00 航程時間軸","action":"agenda","enabled":True},
        {"icon":"🎁","title":"活動商品專區","subtitle":"精選伴手禮與補給品","description":"點擊進入物資艙查看","action":"gift","enabled":True},
        {"icon":"▶","title":"核心引擎啟動","subtitle":"智能全新數位中控系統","description":"三年經營現況影片","action":"video","enabled":True},
        {"icon":"🧭","title":"2026 產業星圖","subtitle":"領航員名冊與每攤機會","description":"產業導覽","action":"map","enabled":True}
    ],
    "theme_background_color": "#061A18",
    "theme_primary_color": "#14B8A6",
    "theme_accent_color": "#5EEAD4",
    "theme_text_color": "#ECFEFF"
}''')
    # ensure columns
    if "'success_card_config': 'LONGTEXT'" not in server:
        server = server.replace(
            "        'projection_subtitle': 'VARCHAR(255)'\n    }",
            "        'projection_subtitle': 'VARCHAR(255)',\n        'map_title': 'VARCHAR(255)',\n        'map_subtitle': 'VARCHAR(255)',\n        'map_image_url': 'LONGTEXT',\n        'map_description': 'LONGTEXT',\n        'map_enabled': 'BOOLEAN DEFAULT TRUE',\n        'success_card_config': 'LONGTEXT',\n        'success_info_cards_config': 'LONGTEXT',\n        'theme_background_color': 'VARCHAR(20)',\n        'theme_primary_color': 'VARCHAR(20)',\n        'theme_accent_color': 'VARCHAR(20)',\n        'theme_text_color': 'VARCHAR(20)'\n    }"
        )
    # json helper
    if 'def _json_loads_maybe' not in server:
        server = server.replace(
            "def _event_config_from_row(row, admin_user, event_key):",
            '''def _json_loads_maybe(value, default):
    if value is None or value == '':
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _json_dumps_if_needed(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value if value is not None else ''


def _event_config_from_row(row, admin_user, event_key):'''
        )
    # parse JSON fields after bools
    if "data['success_card_config'] = _json_loads_maybe" not in server:
        server = server.replace(
            "    for key in ['gift_enabled', 'video_embed_enabled', 'video_enabled', 'flow_enabled']:\n        data[key] = _as_bool(data.get(key), True)",
            "    for key in ['gift_enabled', 'video_embed_enabled', 'video_enabled', 'flow_enabled', 'map_enabled']:\n        data[key] = _as_bool(data.get(key), True)\n    data['success_card_config'] = _json_loads_maybe(data.get('success_card_config'), EXPERIENCE_CONFIG_DEFAULTS.get('success_card_config', {}))\n    data['success_info_cards_config'] = _json_loads_maybe(data.get('success_info_cards_config'), EXPERIENCE_CONFIG_DEFAULTS.get('success_info_cards_config', []))"
        )
    # handle_config POST: REPLACE -> UPSERT partial, preserve columns
    server = server.replace(
        'sql_cfg = "REPLACE INTO event_configs (admin_user, event_key, show_meal_options, map_image_url, banner_image_url) VALUES (%s, %s, %s, %s, %s)"\n                cursor.execute(sql_cfg, (admin_user, event_key, 1, payload.get("map_image_url", ""), payload.get("banner_image_url", "")))',
        'sql_cfg = """\n                    INSERT INTO event_configs (admin_user, event_key, show_meal_options, map_image_url, banner_image_url)\n                    VALUES (%s, %s, %s, %s, %s)\n                    ON DUPLICATE KEY UPDATE\n                        show_meal_options = VALUES(show_meal_options),\n                        map_image_url = VALUES(map_image_url),\n                        banner_image_url = VALUES(banner_image_url)\n                """\n                cursor.execute(sql_cfg, (admin_user, event_key, 1, payload.get("map_image_url", ""), payload.get("banner_image_url", "")))'
    )
    # api_admin_event_config cols add keys
    if "'success_card_config', 'success_info_cards_config'" not in server:
        server = server.replace(
            "            'projection_title', 'projection_subtitle'\n        ]",
            "            'projection_title', 'projection_subtitle',\n            'map_title', 'map_subtitle', 'map_description', 'map_enabled',\n            'success_card_config', 'success_info_cards_config',\n            'theme_background_color', 'theme_primary_color', 'theme_accent_color', 'theme_text_color'\n        ]"
        )
    # JSON dumps before sql execute
    if "data['success_card_config'] = _json_dumps_if_needed" not in server:
        server = server.replace(
            "        map_image_url = payload.get('map_image_url', current.get('map_image_url', ''))\n        banner_image_url = payload.get('banner_image_url', current.get('banner_image_url', ''))",
            "        map_image_url = payload.get('map_image_url', current.get('map_image_url', ''))\n        banner_image_url = payload.get('banner_image_url', current.get('banner_image_url', ''))\n        data['success_card_config'] = _json_dumps_if_needed(data.get('success_card_config'))\n        data['success_info_cards_config'] = _json_dumps_if_needed(data.get('success_info_cards_config'))"
        )
    # checkin meal bug + no fake job_title
    server = server.replace("meal_choice = user['meal_choice'] if is_original else data.get('meal', user['meal_choice'])", "meal_choice = data.get('meal') or user.get('meal_choice') or ''")
    server = server.replace("r['job_title'] = r.get('job_title') or '學員'", "r['job_title'] = r.get('job_title') or ''")
    server = server.replace('"job_title": user.get(\'job_title\') or \'學員\',', '"job_title": user.get(\'job_title\') or \'\',')
    # dashboard logs add fields
    server = server.replace(
        "r.meal_choice,\n                    COALESCE(NULLIF(TRIM(m.industry), ''),",
        "r.meal_choice,\n                    r.job_title,\n                    r.portrait_consent_status,\n                    r.portrait_consent,\n                    COALESCE(NULLIF(TRIM(m.industry), ''),"
    )
    server = server.replace(
        '"meal": r.get(\'meal_choice\') or ""\n        } for r in checked_logs]',
        '"meal": r.get(\'meal_choice\') or "",\n            "job_title": r.get(\'job_title\') or "",\n            "portrait_consent_status": r.get(\'portrait_consent_status\') or "",\n            "portrait_consent": r.get(\'portrait_consent\')\n        } for r in checked_logs]'
    )
    # table detail select/person fields
    server = server.replace(
        "SELECT id, name, phone, company_name, seating_chart, status, checkin_time, meal_choice\n                FROM event_registrations",
        "SELECT id, name, phone, company_name, job_title, seating_chart, status, checkin_time, meal_choice, portrait_consent_status, portrait_consent\n                FROM event_registrations"
    )
    server = server.replace(
        '"company": row.get("company_name") or "",\n                "seat": row.get("seating_chart") or "",\n                "meal": row.get("meal_choice") or "",',
        '"company": row.get("company_name") or "",\n                "job_title": row.get("job_title") or "",\n                "seat": row.get("seating_chart") or "",\n                "meal": row.get("meal_choice") or "",\n                "portrait_consent_status": row.get("portrait_consent_status") or "",\n                "portrait_consent": row.get("portrait_consent"),'
    )
    # stats/meals function replace full block
    new_meal_api = r'''@app.route('/api/stats/meals')
def api_meal_stats():
    """取得餐飲統計與肖像權狀態。備註欄只顯示肖像權，不混特殊飲食。"""
    if not session.get('admin_logged_in'):
        return jsonify({"success": False, "message": "未授權"}), 403

    admin_user, event_key = get_admin_and_event_context()
    conn = get_db_connection()

    def normalize_meal(choice):
        text = str(choice or '').strip()
        compact = re.sub(r'\s+', '', text)
        if not compact:
            return '其他'
        if re.search(r'素|蔬|veg|vegetarian', compact, re.I):
            return '素'
        if re.search(r'葷|肉|meat|non-?veg', compact, re.I):
            return '葷'
        return '其他'

    try:
        ensure_core_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT
                    name,
                    company_name,
                    meal_choice,
                    status,
                    portrait_consent,
                    portrait_consent_status
                FROM event_registrations
                WHERE admin_user = %s AND event_key = %s
            """, (admin_user, event_key))
            rows = cursor.fetchall()

        meals = {"葷": 0, "素": 0, "其他": 0}
        portrait_notes = []
        for row in rows:
            meals[normalize_meal(row.get('meal_choice'))] += 1
            status = (row.get('portrait_consent_status') or '').strip()
            if not status:
                if row.get('portrait_consent') in [1, True, '1']:
                    status = '同意'
                elif row.get('portrait_consent') in [0, False, '0']:
                    status = '不同意'
                else:
                    status = '未填'
            portrait_notes.append({
                "name": row.get('name') or '',
                "company": row.get('company_name') or '',
                "portrait_consent_status": status,
                "note": ('同意肖像權' if status == '同意' else '不同意肖像權' if status == '不同意' else '未填')
            })

        return jsonify({
            "success": True,
            "meals": meals,
            "special_notes": portrait_notes,
            "portrait_notes": portrait_notes
        })
    finally:
        conn.close()

'''
    server = replace_between(server, r"@app\.route\('/api/stats/meals'\)\ndef api_meal_stats\(\):", r"@app\.route\('/api/sheets/list'\)", new_meal_api + "@app.route('/api/sheets/list')")
    # export CSV wording
    server = server.replace("'備註(不吃的)'", "'備註'")
    server = server.replace('"活動 Navigator 設定已儲存"', '"智匯方舟設定已儲存"')
    write(FILES['server'], server)

print('\n完成。建議重新部署 Railway 後開 /api/bootstrap_db 讓欄位自動補齊。')
