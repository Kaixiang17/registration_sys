<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>活動報到系統</title>
    <style>
        :root { --primary: #4f46e5; --primary-hover: #4338ca; --bg: #f8f7f4; --card-bg: #ffffff; --text: #1a1a1a; --text-muted: #666666; --border: #e2e0db; --success: #16a34a; --error: #dc2626; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: "PingFang TC", sans-serif; }
        body { background-color: var(--bg); color: var(--text); line-height: 1.6; min-height: 100vh; }
        header { background: white; border-bottom: 1px solid var(--border); padding: 1rem 0; position: sticky; top: 0; z-index: 100; }
        .header-container { max-width: 1000px; margin: 0 auto; padding: 0 1.5rem; display: flex; justify-content: space-between; align-items: center; }
        .header-title { font-size: 1.25rem; font-weight: 700; }
        main { max-width: 1000px; margin: 0 auto; padding: 3rem 1rem; }
        .state-hidden { display: none !important; }
        .mode-selection-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 2rem; max-width: 950px; margin: 0 auto 3rem; }
        .mode-card { background: white; border: 2px solid #e2e0db; border-radius: 1rem; padding: 2.5rem 1.5rem; text-align: center; cursor: pointer; transition: all 0.3s ease; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05); }
        .mode-card:hover { border-color: var(--primary); transform: translateY(-5px); box-shadow: 0 12px 20px rgba(79, 70, 229, 0.1); }
        .mode-icon { font-size: 3.5rem; margin-bottom: 1.5rem; display: block; }
        .form-card { background: white; border: 1px solid var(--border); border-radius: 1rem; padding: 2.5rem; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); text-align: center; }
        input[type="text"] { width: 100%; padding: 0.875rem 1rem; border: 1px solid var(--border); border-radius: 0.5rem; font-size: 1rem; margin-bottom: 1rem; }
        .button { width: 100%; padding: 0.875rem; border: none; border-radius: 0.5rem; font-weight: 600; cursor: pointer; transition: all 0.2s; }
        .button-primary { background-color: var(--primary); color: white; }
        .meal-option { display: flex; align-items: center; gap: 1rem; padding: 1.25rem; border: 2px solid var(--border); border-radius: 0.75rem; cursor: pointer; margin-bottom: 1rem; }
        .meal-option.selected { border-color: var(--primary); background: rgba(79, 70, 229, 0.05); }
        .api-status { display: flex; align-items: center; gap: 0.5rem; font-size: 0.75rem; }
        .api-status-dot { width: 8px; height: 8px; border-radius: 50%; background-color: #9ca3af; }
        .api-status-dot.connected { background-color: var(--success); }
    </style>
</head>
<body>
    <header>
        <div class="header-container">
            <div onclick="location.reload()" style="cursor:pointer">🏠 首頁</div>
            <div class="header-title">活動報到系統</div>
            <div class="api-status">
                <div id="api-status-dot" class="api-status-dot"></div>
                <span id="api-status-text">連線中...</span>
            </div>
        </div>
    </header>

    <main>
        <div id="modeState">
            <h1 style="text-align:center; margin-bottom: 3rem;">歡迎參加活動</h1>
            <div class="mode-selection-grid">
                <div class="mode-card" onclick="setMode('individual')">
                    <span class="mode-icon">👤</span><h2>個人報到</h2>
                </div>
                <div class="mode-card" onclick="setMode('group')">
                    <span class="mode-icon">🏢</span><h2>公司團體報到</h2>
                </div>
                <div class="mode-card" onclick="location.href='/商品頁面.html'">
                    <span class="mode-icon">🛍️</span><h2>活動商品專區</h2>
                </div>
            </div>
        </div>

        <div id="actionState" class="state-hidden">
            <div class="form-card" style="max-width:500px; margin:0 auto;">
                <button onclick="setAppState('mode')" style="float:left; background:none; border:none; color:gray; cursor:pointer">← 返回</button>
                <h2 id="actionTitle" style="margin-bottom:1.5rem;">驗證</h2>
                <input type="text" id="actionInput" placeholder="請輸入資訊">
                <button class="button button-primary" onclick="handleAction()">下一步</button>
            </div>
        </div>

        <div id="mealState" class="state-hidden">
            <div class="form-card" style="max-width:500px; margin:0 auto;">
                <h2>🍱 餐食選擇</h2>
                <div class="meal-option" id="m-葷食" onclick="selectMeal('葷食')">🍖 葷食</div>
                <div class="meal-option" id="m-素食" onclick="selectMeal('素食')">🥗 素食</div>
                <button id="finalBtn" class="button button-primary" disabled onclick="submitCheckin()">確認報到</button>
            </div>
        </div>

        <div id="successState" class="state-hidden">
            <div class="form-card" style="max-width:500px; margin:0 auto;">
                <div style="font-size:3rem; color:green">✓</div>
                <h2>報到成功！</h2>
                <p style="margin:1rem 0">資料已同步。<strong>1.5 秒後自動跳轉商品頁面...</strong></p>
                <button class="button button-primary" onclick="location.href='/商品頁面.html'">🛍️ 立即前往商品頁</button>
            </div>
        </div>
    </main>

    <script>
        const API_BASE = '/api';
        let checkMode = '', selectedUser = null, selectedMeal = '';

        function setAppState(state) {
            ['mode', 'action', 'meal', 'success'].forEach(s => document.getElementById(s+'State').classList.add('state-hidden'));
            document.getElementById(state+'State').classList.remove('state-hidden');
        }

        function setMode(m) {
            checkMode = m;
            document.getElementById('actionTitle').textContent = m === 'individual' ? '請輸入姓名或手機' : '請輸入公司名稱';
            setAppState('action');
        }

        async function handleAction() {
            const val = document.getElementById('actionInput').value.trim();
            const res = await fetch(`${API_BASE}/search/${checkMode === 'individual' ? 'name' : 'company'}?${checkMode === 'individual' ? 'name' : 'company'}=${val}`);
            const json = await res.json();
            if (json.data && json.data.length > 0) {
                selectedUser = json.data[0];
                setAppState('meal');
            } else alert('查無資料');
        }

        function selectMeal(m) {
            selectedMeal = m;
            document.querySelectorAll('.meal-option').forEach(el => el.classList.remove('selected'));
            document.getElementById('m-'+m).classList.add('selected');
            document.getElementById('finalBtn').disabled = false;
        }

        async function submitCheckin() {
            const res = await fetch(`${API_BASE}/checkin/${selectedUser.id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ meal: selectedMeal, name: selectedUser.name })
            });
            if (res.ok) {
                setAppState('success');
                setTimeout(() => location.href = '/商品頁面.html', 1500);
            }
        }

        async function init() {
            try {
                const res = await fetch(`${API_BASE}/config`);
                if (res.ok) {
                    document.getElementById('api-status-dot').classList.add('connected');
                    document.getElementById('api-status-text').textContent = '已連線至雲端';
                }
            } catch (e) {}
        }
        window.onload = init;
    </script>
</body>
</html>
