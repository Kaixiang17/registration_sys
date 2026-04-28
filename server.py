<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>智慧方舟 - 報到系統</title>
    <link rel="stylesheet" href="smart-ark-theme.css">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: "Michroma", "Stick", sans-serif; }
        body { color: #fff; line-height: 1.6; }
        main { max-width: 1200px; margin: 0 auto; padding: 3rem 1.5rem; }
        .state-hidden { display: none !important; }
        .mode-selection-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 2.5rem; }
        .mode-card { padding: 4.5rem 2rem; text-align: center; cursor: pointer; border-radius: 1.5rem; min-height: 380px; }
        .form-card { max-width: 550px; margin: 0 auto; padding: 3rem; text-align: center; border-radius: 1.5rem; }
        .btn-back-nav { background: none; border: 1px solid var(--accent-gray); color: #888; padding: 0.5rem 1rem; border-radius: 0.5rem; cursor: pointer; margin-bottom: 1rem; }
        .group-item { padding: 1.2rem; border-bottom: 1px solid rgba(0,229,255,0.2); cursor: pointer; transition: 0.3s; text-align: left; }
        .group-item:hover { background: rgba(0,229,255,0.1); }
        #finalBtn { width: 100%; padding: 1.5rem !important; font-size: 1.4rem !important; }
        .info-box { text-align: left; padding: 1.5rem; border-radius: 1rem; margin: 1.5rem 0; border: 1px solid var(--text-dark); background: rgba(0,0,0,0.5); }
    </style>
</head>
<body class="circuit-background">
    <div class="ark-background"></div>
    <header class="panel-border" style="border-radius:0; background:rgba(10,20,35,0.9); padding:1.2rem;">
        <div style="max-width:1200px; margin:0 auto; display:flex; justify-content:space-between; align-items:center;">
            <div onclick="location.reload()" style="cursor:pointer; color:var(--text-dark); font-weight:bold;">🏠 方舟首頁</div>
            <div class="neon-text" style="font-size:1.4rem;">COMMAND CENTER</div>
            <div id="status" style="font-size:0.8rem; color:#888;">🛰️ 衛星連線中</div>
        </div>
    </header>

    <main>
        <div id="modeState">
            <h1 class="neon-text" style="text-align:center; margin-bottom:3.5rem; font-size:2.8rem; display:block;">登艦模組選擇</h1>
            <div class="mode-selection-grid">
                <div class="mode-card cyber-card cyber-hover" onclick="setMode('individual')">
                    <div style="font-size:6rem;">👤</div>
                    <h2 class="neon-text">個人驗證報到</h2>
                    <p>輸入電話、姓名或 Email 登艦</p>
                </div>
                <div class="mode-card cyber-card cyber-hover" onclick="setMode('company_individual')">
                    <div style="font-size:6rem;">🏢</div>
                    <h2 class="neon-text">公司個人報到</h2>
                    <p>依公司名搜尋並選擇人員</p>
                </div>
                <div class="mode-card cyber-card cyber-hover" onclick="showProducts()">
                    <div style="font-size:6rem;">🛍️</div>
                    <h2 class="neon-text">補給展示專區</h2>
                    <p>瀏覽方舟配置物資規格</p>
                </div>
            </div>
        </div>

        <div id="actionState" class="state-hidden">
            <button class="btn-back-nav" onclick="setAppState('mode')">← 返回</button>
            <div class="form-card cyber-card">
                <h2 id="actionTitle" class="neon-text" style="margin-bottom:2rem;">身分驗證</h2>
                <input type="text" id="actionInput" class="cyber-input" placeholder="請輸入資訊">
                <p style="color:#888; font-size:0.9rem; margin-top:1rem; text-align:left;">* 系統將自動比對資料庫</p>
                <button class="cyber-button" style="width:100%; margin-top:2rem;" onclick="handleAction()"><span>啟動搜尋</span></button>
            </div>
        </div>

        <div id="groupListState" class="state-hidden">
            <button class="btn-back-nav" onclick="setAppState('action')">← 返回</button>
            <div class="form-card cyber-card" style="max-width:700px;">
                <h2 id="groupName" class="neon-text" style="margin-bottom:1.5rem;">請選擇報到人員</h2>
                <div id="groupList" class="panel-border" style="background:rgba(0,0,0,0.3); border-radius:1rem; max-height:400px; overflow-y:auto;"></div>
            </div>
        </div>

        <div id="mealState" class="state-hidden">
            <button class="btn-back-nav" onclick="setAppState('mode')">← 回首頁</button>
            <div class="form-card cyber-card">
                <h2 class="neon-text" style="margin-bottom:2rem;">🍱 能量補給配置</h2>
                <div id="mealOptions"></div>
                <button id="finalBtn" class="cyber-button" disabled onclick="submitCheckin()"><span>確認登艦</span></button>
            </div>
        </div>

        <div id="successState" class="state-hidden">
            <div class="form-card cyber-card">
                <div id="successIcon" style="font-size:6rem; line-height:1;">✓</div>
                <h2 id="successTitle" class="neon-text" style="margin-bottom:2rem;">登艦成功！</h2>
                <div id="successInfo" class="info-box panel-border"></div>
                <button class="cyber-button" onclick="location.reload()"><span>🏠 回到首頁</span></button>
            </div>
        </div>
    </main>

    <script>
        const API_BASE = '/api';
        let checkMode = '', selectedUser = null, matchedCompanies = [], systemConfig = null;

        function setAppState(s) {
            ['mode', 'action', 'groupList', 'meal', 'success'].forEach(x => document.getElementById(x+'State').classList.add('state-hidden'));
            document.getElementById(s+'State').classList.remove('state-hidden');
            window.scrollTo(0,0);
        }

        function setMode(m) {
            checkMode = m;
            document.getElementById('actionTitle').textContent = m === 'individual' ? '個人驗證' : '公司搜尋';
            document.getElementById('actionInput').value = '';
            document.getElementById('actionInput').placeholder = m === 'individual' ? '姓名 / 電話 / Email' : '公司名稱關鍵字';
            setAppState('action');
        }

        async function handleAction() {
            const val = document.getElementById('actionInput').value.trim();
            if(!val) return;
            const method = checkMode === 'individual' ? 'keyword' : 'company';
            try {
                const res = await fetch(`${API_BASE}/search/${method}?${method}=${encodeURIComponent(val)}`);
                const json = await res.json();
                if (json.data.length > 0) {
                    if (checkMode === 'individual' && json.data.length === 1) { 
                        selectedUser = json.data[0]; renderMealUI(); 
                    } else {
                        document.getElementById('groupName').textContent = checkMode === 'individual' ? '找到多位人員，請選擇' : '艦隊人員名單';
                        document.getElementById('groupList').innerHTML = json.data.map(u => `
                            <div class="group-item" onclick="selectSingleUser('${u.id}', '${u.name}')">
                                <b style="font-size:1.2rem;">${u.name}</b> <br> <span style="color:#aaa;">${u.company} | ${u.status === 'checked_in' ? '🟢 已登艦' : '⚪ 待命'}</span>
                            </div>`).join('');
                        window.tempData = json.data;
                        setAppState('groupList');
                    }
                } else alert('查無資料');
            } catch(e) { alert('連線異常'); }
        }

        function selectSingleUser(id) {
            selectedUser = window.tempData.find(u => u.id === id);
            renderMealUI();
        }

        function renderMealUI() {
            const options = (systemConfig && systemConfig.meal_types) ? systemConfig.meal_types : ['葷食', '素食', '不須用餐'];
            document.getElementById('mealOptions').innerHTML = options.map(m => `
                <div class="cyber-card cyber-hover panel-border" style="padding:1.2rem; margin-bottom:1rem; cursor:pointer;" onclick="selectMeal('${m}', this)">${m}</div>`).join('');
            setAppState('meal');
        }

        function selectMeal(m, el) {
            window.selectedMeal = m;
            document.querySelectorAll('#mealOptions div').forEach(d => { d.style.borderColor='rgba(0,229,255,0.3)'; d.style.background='rgba(0,0,0,0.4)'; });
            el.style.borderColor='var(--text-dark)'; el.style.background='rgba(0,229,255,0.15)';
            document.getElementById('finalBtn').disabled = false;
        }

        async function submitCheckin() {
            const btn = document.getElementById('finalBtn');
            btn.disabled = true; btn.innerHTML = '<span>📡 傳輸中...</span>';
            try {
                const res = await fetch(`${API_BASE}/checkin/${selectedUser.id}`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({meal:window.selectedMeal}) });
                const json = await res.json();
                const info = document.getElementById('successInfo');
                
                if (json.success) {
                    document.getElementById('successTitle').textContent = '登艦成功！';
                    document.getElementById('successIcon').textContent = '✓';
                    document.getElementById('successIcon').style.color = 'var(--text-dark)';
                    info.innerHTML = `<p><b>登艦人員：</b>${json.data.name}</p><p><b>艦隊單位：</b>${json.data.company}</p><p><b>能量配置：</b>${json.data.meal}</p><p><b>登艦時間：</b>${json.data.checkedInAt}</p>`;
                } else if (json.error === 'already_done') {
                    // ★ 關鍵：重複報到顯示
                    document.getElementById('successTitle').textContent = '重複登艦紀錄';
                    document.getElementById('successIcon').textContent = '⚠️';
                    document.getElementById('successIcon').style.color = '#ff4d4d';
                    info.innerHTML = `<div style="color:#ff4d4d; font-weight:bold; border-bottom:1px solid #ff4d4d; margin-bottom:1rem;">此探險者先前已完成登艦程序</div>
                        <p><b>姓名：</b>${json.data.name}</p><p><b>初次登艦：</b>${json.data.checkedInAt}</p><p><b>配置補給：</b>${json.data.meal}</p><p><b>所屬單位：</b>${json.data.company}</p>`;
                }
                setAppState('success');
            } catch(e) { alert('同步失敗'); btn.disabled=false; }
        }

        window.onload = async () => {
            const res = await fetch(`${API_BASE}/config`);
            if (res.ok) systemConfig = await res.json();
        };
    </script>
</body>
</html>
