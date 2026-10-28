import sys

portal_html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>PocketClaw · 家长管理控制台</title>
<style>
  :root {
    --primary: #2563eb;
    --primary-light: #eff6ff;
    --primary-border: #bfdbfe;
    --bg: #f8fafc;
    --card: #ffffff;
    --text: #0f172a;
    --muted: #64748b;
    --border: #e2e8f0;
    --success: #16a34a;
    --success-bg: #dcfce7;
    --tag-chinese: #ea580c;
    --tag-math: #2563eb;
    --tag-english: #7c3aed;
    --tag-sci: #0d9488;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 12px 12px 80px 12px;
    line-height: 1.5;
  }
  .container { max-width: 680px; margin: 0 auto; }
  header {
    background: var(--card);
    border-radius: 14px;
    padding: 14px 16px;
    margin-bottom: 12px;
    border: 1px solid var(--border);
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header-title { font-size: 17px; font-weight: 700; display: flex; align-items: center; gap: 6px; }
  .header-status { font-size: 12px; display: flex; align-items: center; gap: 8px; }
  .badge { display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .badge-online { background: var(--success-bg); color: var(--success); }
  .badge-offline { background: #f1f5f9; color: var(--muted); }
  .badge-battery { background: #fef3c7; color: #b45309; }

  /* Tab Navigation */
  .tabs-nav {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding-bottom: 8px;
    margin-bottom: 12px;
    scrollbar-width: none;
  }
  .tabs-nav::-webkit-scrollbar { display: none; }
  .tab-btn {
    white-space: nowrap;
    padding: 7px 12px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 600;
    border: 1px solid var(--border);
    background: var(--card);
    color: var(--muted);
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .tab-btn.active {
    background: var(--primary);
    color: #fff;
    border-color: var(--primary);
    box-shadow: 0 2px 4px rgba(37, 99, 235, 0.2);
  }

  .tab-content { display: none; }
  .tab-content.active { display: block; animation: fadeIn 0.2s ease; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

  .card {
    background: var(--card);
    border-radius: 14px;
    padding: 16px;
    margin-bottom: 12px;
    border: 1px solid var(--border);
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
  }
  .card-title {
    font-size: 15px;
    font-weight: 600;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .card-subtitle { font-size: 12px; color: var(--muted); font-weight: normal; }

  textarea, input[type="text"] {
    width: 100%;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--border);
    font-size: 14px;
    outline: none;
    font-family: inherit;
  }
  textarea:focus, input[type="text"]:focus { border-color: var(--primary); }
  textarea { resize: vertical; min-height: 80px; }

  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    padding: 9px 14px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    border: none;
    cursor: pointer;
    transition: background 0.15s ease;
  }
  .btn-primary { background: var(--primary); color: #fff; }
  .btn-primary:active { opacity: 0.85; }
  .btn-secondary { background: #f1f5f9; color: var(--text); border: 1px solid var(--border); }
  .btn-danger { background: #fee2e2; color: #dc2626; }
  .btn-sm { padding: 5px 10px; font-size: 12px; border-radius: 6px; }
  .btn-block { width: 100%; }

  .btn-row { display: flex; gap: 8px; margin-top: 10px; }

  /* Tasks list */
  .task-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 8px;
    border-bottom: 1px solid #f1f5f9;
  }
  .task-item:last-child { border-bottom: none; }
  .task-label { display: flex; align-items: center; gap: 8px; cursor: pointer; flex: 1; }
  .task-text.done { text-decoration: line-through; color: var(--muted); }
  .subject-pill {
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
  }
  .sub-语文 { background: #ffedd5; color: var(--tag-chinese); }
  .sub-数学 { background: #dbeafe; color: var(--tag-math); }
  .sub-英语 { background: #ede9fe; color: var(--tag-english); }
  .sub-科学 { background: #ccfbf1; color: var(--tag-sci); }

  /* Grid selection items */
  .grid-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 10px; }
  .grid-card {
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px;
    background: #fff;
    cursor: pointer;
    transition: all 0.15s ease;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }
  .grid-card:hover { border-color: var(--primary-border); background: var(--primary-light); }
  .grid-card.active { border-color: var(--primary); background: var(--primary-light); box-shadow: 0 0 0 2px rgba(37,99,235,0.2); }
  .grid-card-title { font-weight: 700; font-size: 14px; margin-bottom: 4px; }
  .grid-card-desc { font-size: 12px; color: var(--muted); line-height: 1.4; }

  /* Pill group */
  .pill-group { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
  .pill-btn {
    padding: 5px 12px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 500;
    border: 1px solid var(--border);
    background: #fff;
    cursor: pointer;
  }
  .pill-btn.active {
    background: var(--primary);
    color: #fff;
    border-color: var(--primary);
  }

  /* Toast Notification */
  #toast {
    position: fixed;
    top: 20px;
    left: 50%;
    transform: translateX(-50%) translateY(-20px);
    background: rgba(15, 23, 42, 0.9);
    color: #fff;
    padding: 10px 18px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 500;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    opacity: 0;
    pointer-events: none;
    transition: all 0.25s ease;
    z-index: 9999;
  }
  #toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
</style>
</head>
<body>
<div class="container">
  <div id="toast"></div>

  <!-- Header -->
  <header>
    <div class="header-title">
      <span>🦞</span>
      <span>PocketClaw 控制台</span>
    </div>
    <div class="header-status">
      <span id="badgeOnline" class="badge badge-offline">检测中...</span>
      <span id="badgeBattery" class="badge badge-battery">🔋 --</span>
    </div>
  </header>

  <!-- Horizontal Tabs -->
  <div class="tabs-nav">
    <button class="tab-btn active" onclick="switchTab('tasks')">📋 作业清单</button>
    <button class="tab-btn" onclick="switchTab('dictation')">✍️ 生字听写</button>
    <button class="tab-btn" onclick="switchTab('poem')">📜 课内古诗</button>
    <button class="tab-btn" onclick="switchTab('arith')">🔢 口算闯关</button>
    <button class="tab-btn" onclick="switchTab('vocab')">🔤 英语生词</button>
    <button class="tab-btn" onclick="switchTab('reading')">📖 英语短文</button>
    <button class="tab-btn" onclick="switchTab('message')">💌 亲子留言</button>
    <button class="tab-btn" onclick="switchTab('report')">📊 学情报表</button>
  </div>

  <!-- Tab 1: 作业清单 -->
  <div id="tab-tasks" class="tab-content active">
    <div class="card">
      <div class="card-title">
        <span>微信班级群作业一键粘贴</span>
        <span class="card-subtitle">智能识别学科与序号</span>
      </div>
      <textarea id="rawTasksInput" placeholder="直接复制老师在微信群发的作业通知，例如：
1. 语文：背诵古诗《静夜思》
2. 数学：完成练习册第24页
3. 英语：大声朗读第一单元单词"></textarea>
      <div class="btn-row">
        <button class="btn btn-primary" onclick="submitQuickInput(false)">➕ 解析并追加</button>
        <button class="btn btn-secondary" onclick="submitQuickInput(true)">🔄 清空并覆盖</button>
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        <span>今日作业进度 (<span id="taskStats">0/0</span>)</span>
        <button class="btn btn-secondary btn-sm" onclick="clearDoneTasks()">清空已打勾</button>
      </div>
      <div style="display:flex; gap:8px; margin-bottom:12px;">
        <input type="text" id="singleTaskInput" placeholder="输入一条新作业...">
        <button class="btn btn-primary btn-sm" onclick="addSingleTask()">添加</button>
      </div>
      <div id="taskList"></div>
    </div>
  </div>

  <!-- Tab 2: 生字听写 -->
  <div id="tab-dictation" class="tab-content">
    <div class="card">
      <div class="card-title">
        <span>语文生字词听写设置</span>
        <span class="card-subtitle">便利贴报词·防剧透·自主听写</span>
      </div>
      <p style="font-size:12px; color:var(--muted); margin-bottom:8px;">
        输入本次听写的词语（用逗号或换行分隔），下发后便签只显示占位符与拼音，防剧透报词。
      </p>
      <div class="pill-group">
        <button class="pill-btn" onclick="setPresetWords('三年级精选')">三年级精选</button>
        <button class="pill-btn" onclick="setPresetWords('二年级字词')">二年级字词</button>
        <button class="pill-btn" onclick="setPresetWords('期中易错词')">期中易错词</button>
      </div>
      <textarea id="dictationInput" placeholder="温暖, 认真, 遵守, 奇迹, 灿烂, 严寒, 准备, 丰富"></textarea>
      <div class="btn-row">
        <button class="btn btn-primary btn-block" onclick="saveDictation()">🚀 保存并下发到 Note 4</button>
      </div>
    </div>
    <div class="card">
      <div class="card-title"><span>当前待听写字词库 (<span id="dictCount">0</span>个)</span></div>
      <div id="dictationPreview" style="font-size:13px; color:var(--text); line-height:1.8;"></div>
    </div>
  </div>

  <!-- Tab 3: 古诗背诵 -->
  <div id="tab-poem" class="tab-content">
    <div class="card">
      <div class="card-title">
        <span>课内古诗背诵质检官</span>
        <span class="badge badge-online" id="curPoemBadge">当前：静夜思</span>
      </div>
      <p style="font-size:12px; color:var(--muted); margin-bottom:12px;">
        选择当前考核的古诗。孩子按住确认键背诵，AI 逐句智能质检纠错，不提前报原句。
      </p>
      <div class="grid-list" id="poemGrid"></div>
    </div>
  </div>

  <!-- Tab 4: 口算闯关 -->
  <div id="tab-arith" class="tab-content">
    <div class="card">
      <div class="card-title"><span>数学速算闯关配置</span></div>
      <p style="font-size:12px; color:var(--muted); margin-bottom:12px;">设置闯关年级与题目数量，自动生成算式闪卡：</p>
      <div style="margin-bottom:12px;">
        <label style="font-size:13px; font-weight:600; display:block; margin-bottom:6px;">年级难度：</label>
        <div class="pill-group" id="arithGradeGroup">
          <button class="pill-btn" data-val="1" onclick="selectArithPill('grade', 1)">一年级 (20以内加减)</button>
          <button class="pill-btn active" data-val="2" onclick="selectArithPill('grade', 2)">二年级 (表内乘除加减)</button>
          <button class="pill-btn" data-val="3" onclick="selectArithPill('grade', 3)">三年级 (百以内加减乘)</button>
          <button class="pill-btn" data-val="4" onclick="selectArithPill('grade', 4)">四年级 (四则混合运算)</button>
        </div>
      </div>
      <div style="margin-bottom:12px;">
        <label style="font-size:13px; font-weight:600; display:block; margin-bottom:6px;">题量：</label>
        <div class="pill-group" id="arithCountGroup">
          <button class="pill-btn active" data-val="10" onclick="selectArithPill('count', 10)">10 道题</button>
          <button class="pill-btn" data-val="20" onclick="selectArithPill('count', 20)">20 道题</button>
        </div>
      </div>
      <button class="btn btn-primary btn-block" onclick="saveArithConfig()">💾 保存口算配置并推屏</button>
    </div>
  </div>

  <!-- Tab 5: 英语生词 -->
  <div id="tab-vocab" class="tab-content">
    <div class="card">
      <div class="card-title"><span>添加新单词</span></div>
      <div style="display:grid; grid-template-columns: 1fr 1fr; gap:8px; margin-bottom:8px;">
        <input type="text" id="newWord" placeholder="单词 (例: apple)">
        <input type="text" id="newMeaning" placeholder="释义 (例: 苹果)">
      </div>
      <input type="text" id="newExample" placeholder="例句 (例: I eat an apple.)" style="margin-bottom:8px;">
      <input type="text" id="newExampleCn" placeholder="例句中文 (例: 我吃一个苹果。)" style="margin-bottom:12px;">
      <button class="btn btn-primary btn-block" onclick="addVocabWord()">➕ 添加到生词本</button>
    </div>
    <div class="card">
      <div class="card-title"><span>生词本词汇列表 (<span id="vocabCount">0</span>)</span></div>
      <div id="vocabList" style="display:flex; flex-direction:column; gap:8px;"></div>
    </div>
  </div>

  <!-- Tab 6: 英语短文 -->
  <div id="tab-reading" class="tab-content">
    <div class="card">
      <div class="card-title">
        <span>小学英语分级阅读篇目</span>
        <span class="badge badge-online" id="curReadingBadge">当前篇目</span>
      </div>
      <p style="font-size:12px; color:var(--muted); margin-bottom:12px;">
        墨水屏完整展示整篇短文，按▲/▼键选句时黑底白字高亮，自动纯正英音朗读，长按跟读评测。
      </p>
      <div id="readingArticlesList" style="display:flex; flex-direction:column; gap:10px;"></div>
    </div>
  </div>

  <!-- Tab 7: 亲子留言 -->
  <div id="tab-message" class="tab-content">
    <div class="card">
      <div class="card-title">
        <span>💌 远程亲子温馨留言</span>
        <span class="card-subtitle">实时弹出 Note 4 墨水屏</span>
      </div>
      <p style="font-size:12px; color:var(--muted); margin-bottom:8px;">点击快捷留言或输入悄悄话，设备端会播报提示并全屏展示温暖留言：</p>
      <div class="pill-group">
        <button class="pill-btn" onclick="setMessageText('宝贝今天写作业特别认真，为你点赞！')">为你点赞 👍</button>
        <button class="pill-btn" onclick="setMessageText('写完作业早点休息，明天有你爱吃的草莓！')">早点休息 🍓</button>
        <button class="pill-btn" onclick="setMessageText('遇到难题别着急，深呼吸慢慢想，相信你可以！')">别着急加油 💪</button>
      </div>
      <textarea id="parentMsgInput" placeholder="给宝贝写一句鼓励的话..."></textarea>
      <div class="btn-row">
        <button class="btn btn-primary btn-block" onclick="sendParentMessage()">🚀 立即发送到墨水屏</button>
      </div>
    </div>
  </div>

  <!-- Tab 8: 学情报表 -->
  <div id="tab-report" class="tab-content">
    <div class="card">
      <div class="card-title"><span>📊 今日学习成果看板</span></div>
      <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:8px; text-align:center; margin-bottom:16px;">
        <div style="background:#f1f5f9; padding:12px; border-radius:10px;">
          <div style="font-size:20px; font-weight:700; color:var(--primary);" id="repTaskRate">0%</div>
          <div style="font-size:11px; color:var(--muted);">作业完成率</div>
        </div>
        <div style="background:#f1f5f9; padding:12px; border-radius:10px;">
          <div style="font-size:20px; font-weight:700; color:#ea580c;" id="repFocusMin">0m</div>
          <div style="font-size:11px; color:var(--muted);">专注学习时长</div>
        </div>
        <div style="background:#f1f5f9; padding:12px; border-radius:10px;">
          <div style="font-size:20px; font-weight:700; color:var(--success);" id="repVocabCnt">0</div>
          <div style="font-size:11px; color:var(--muted);">词汇储备</div>
        </div>
      </div>
      <div class="card-title" style="margin-bottom:8px;"><span>小学生成长成就勋章</span></div>
      <div id="badgesGrid" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(130px, 1fr)); gap:8px;"></div>
    </div>
  </div>

</div>

<script>
let state = {
  currentTab: 'tasks',
  arithGrade: 2,
  arithCount: 10,
  poems: [],
  readingArticles: [],
  curPoemTitle: '',
  curReadingId: ''
};

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}

function switchTab(name) {
  state.currentTab = name;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  event.target.classList.add('active');
  const target = document.getElementById('tab-' + name);
  if (target) target.classList.add('active');
}

async function loadOverview() {
  try {
    const res = await fetch('/api/pocketclaw/mp/overview');
    const data = await res.json();
    if (!data.ok) return;

    // Header badges
    const bOnline = document.getElementById('badgeOnline');
    if (data.online) {
      bOnline.className = 'badge badge-online';
      bOnline.textContent = '🟢 设备在线';
    } else {
      bOnline.className = 'badge badge-offline';
      bOnline.textContent = '⚪ 待命中';
    }
    const bBat = document.getElementById('badgeBattery');
    bBat.textContent = '🔋 ' + (data.battery !== null ? data.battery + '%' : '已接电');

    // Tasks tab
    document.getElementById('taskStats').textContent = data.task_stats.done + '/' + data.task_stats.total;
    renderTasks(data.tasks);

    // Dictation tab
    document.getElementById('dictCount').textContent = data.dictation_words.length;
    renderDictation(data.dictation_words);

    // Poem badge
    state.curPoemTitle = data.poem.title;
    document.getElementById('curPoemBadge').textContent = '当前背诵：' + data.poem.title;

    // Vocab count & words
    document.getElementById('vocabCount').textContent = data.vocab_count;
    renderVocab(data.vocab_words);

    // Reading badge
    state.curReadingId = data.reading.id;
    document.getElementById('curReadingBadge').textContent = '当前阅读：《' + data.reading.title + '》';

    // Report
    document.getElementById('repTaskRate').textContent =
      data.task_stats.total > 0 ? Math.round(data.task_stats.done / data.task_stats.total * 100) + '%' : '100%';
    document.getElementById('repFocusMin').textContent = data.focus_today_min + 'm';
    document.getElementById('repVocabCnt').textContent = data.vocab_count;
  } catch (e) {
    console.error('loadOverview error:', e);
  }
}

function renderTasks(tasks) {
  const list = document.getElementById('taskList');
  if (!tasks || tasks.length === 0) {
    list.innerHTML = '<div style="color:var(--muted); font-size:13px; text-align:center; padding:16px;">今日暂无作业，可以粘贴班级群通知添加～</div>';
    return;
  }
  list.innerHTML = tasks.map(t => `
    <div class="task-item">
      <div class="task-label" onclick="toggleTask('${t.id}')">
        <input type="checkbox" ${t.done ? 'checked' : ''} style="cursor:pointer; width:16px; height:16px;">
        <span class="subject-pill sub-${t.subject || '通用'}">${t.subject || '通用'}</span>
        <span class="task-text ${t.done ? 'done' : ''}">${t.text}</span>
      </div>
      <button class="btn btn-secondary btn-sm" onclick="deleteTask('${t.id}')" style="padding:2px 6px;">✕</button>
    </div>
  `).join('');
}

function renderDictation(words) {
  const p = document.getElementById('dictationPreview');
  if (!words || words.length === 0) {
    p.textContent = '暂无自定义字词';
    return;
  }
  p.innerHTML = words.map((w, i) => `<span style="display:inline-block; margin-right:12px; background:#f1f5f9; padding:2px 8px; border-radius:6px; margin-bottom:4px;">${i+1}. ${w[0]}</span>`).join('');
}

function renderVocab(words) {
  const el = document.getElementById('vocabList');
  if (!words || words.length === 0) {
    el.innerHTML = '<div style="font-size:12px; color:var(--muted);">生词本为空，可在上方添加新词。</div>';
    return;
  }
  el.innerHTML = words.map(w => `
    <div style="border:1px solid var(--border); border-radius:8px; padding:8px 12px; display:flex; justify-content:space-between; align-items:center;">
      <div>
        <div style="font-weight:700; font-size:14px; color:var(--primary);">${w.word} <span style="font-size:12px; font-weight:normal; color:var(--text); margin-left:6px;">${w.meaning}</span></div>
        ${w.example ? `<div style="font-size:11px; color:var(--muted); margin-top:2px;">例: ${w.example}</div>` : ''}
      </div>
      <button class="btn btn-danger btn-sm" onclick="deleteVocab('${w.id}')">删除</button>
    </div>
  `).join('');
}

async function loadPoems() {
  const res = await fetch('/api/pocketclaw/mp/poems');
  const data = await res.json();
  if (!data.ok) return;
  state.poems = data.poems;
  const grid = document.getElementById('poemGrid');
  grid.innerHTML = data.poems.map(p => `
    <div class="grid-card ${p.title === state.curPoemTitle ? 'active' : ''}" onclick="setPoemTarget('${p.title}')">
      <div>
        <div class="grid-card-title">${p.title} <span style="font-size:11px; font-weight:normal; color:var(--muted);">${p.author}〔${p.dynasty}〕</span></div>
        <div class="grid-card-desc">${p.lines[0]} ${p.lines[1] || ''}</div>
      </div>
      <button class="btn ${p.title === state.curPoemTitle ? 'btn-primary' : 'btn-secondary'} btn-sm" style="margin-top:8px;">
        ${p.title === state.curPoemTitle ? '✓ 考核中' : '设为背诵'}
      </button>
    </div>
  `).join('');
}

async function loadReadingArticles() {
  const res = await fetch('/api/pocketclaw/mp/reading');
  const data = await res.json();
  if (!data.ok) return;
  const list = document.getElementById('readingArticlesList');
  list.innerHTML = data.articles.map(a => `
    <div class="grid-card ${a.id === state.curReadingId ? 'active' : ''}" style="margin-bottom:8px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <span class="grid-card-title">《${a.title}》</span>
          <span class="badge" style="background:#ede9fe; color:var(--tag-english); margin-left:6px;">${a.level}</span>
        </div>
        <button class="btn ${a.id === state.curReadingId ? 'btn-primary' : 'btn-secondary'} btn-sm" onclick="setReadingTarget('${a.id}')">
          ${a.id === state.curReadingId ? '✓ 阅读中' : '设为当前阅读'}
        </button>
      </div>
      <div style="font-size:12px; color:var(--muted); margin-top:6px; line-height:1.6;">
        ${a.sentences.slice(0, 2).map((s, i) => `<div>${i+1}. ${s[0]} (${s[1]})</div>`).join('')}
        ${a.sentences.length > 2 ? `<div style="color:var(--primary); font-size:11px;">共 ${a.sentences.length} 句，点击设为阅读即刻在 Note 4 上逐句学习...</div>` : ''}
      </div>
    </div>
  `).join('');
}

async function loadReport() {
  const res = await fetch('/api/pocketclaw/mp/report');
  const data = await res.json();
  if (!data.ok) return;
  const grid = document.getElementById('badgesGrid');
  grid.innerHTML = data.badges.map(b => `
    <div style="border:1px solid ${b.unlocked ? '#bbf7d0' : 'var(--border)'}; background:${b.unlocked ? '#f0fdf4' : '#f8fafc'}; border-radius:10px; padding:10px; text-align:center;">
      <div style="font-size:24px;">${b.icon}</div>
      <div style="font-size:13px; font-weight:700; color:${b.unlocked ? 'var(--text)' : 'var(--muted)'}; margin:4px 0 2px 0;">${b.title}</div>
      <div style="font-size:11px; color:var(--muted);">${b.desc}</div>
      <div style="font-size:10px; font-weight:600; margin-top:4px; color:${b.unlocked ? 'var(--success)' : 'var(--muted)'};">
        ${b.unlocked ? '★ 已点亮' : '○ 练习中'}
      </div>
    </div>
  `).join('');
}

// Actions
async function submitQuickInput(clearExisting) {
  const raw = document.getElementById('rawTasksInput').value;
  if (!raw.trim()) { showToast('请先输入作业内容'); return; }
  const res = await fetch('/api/pocketclaw/parent/quick_input', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_tasks: raw, clear_existing: clearExisting })
  });
  const data = await res.json();
  if (data.ok) {
    document.getElementById('rawTasksInput').value = '';
    showToast('解析成功，已实时推送到 Note 4 墨水屏！');
    loadOverview();
  }
}

async function addSingleTask() {
  const inp = document.getElementById('singleTaskInput');
  const text = inp.value.trim();
  if (!text) return;
  await fetch('/api/pocketclaw/mp/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: text })
  });
  inp.value = '';
  showToast('作业已添加并同步到设备！');
  loadOverview();
}

async function toggleTask(id) {
  await fetch('/api/pocketclaw/mp/tasks/' + id + '/toggle', { method: 'POST' });
  loadOverview();
}

async function deleteTask(id) {
  await fetch('/api/pocketclaw/mp/tasks/' + id, { method: 'DELETE' });
  loadOverview();
}

async function clearDoneTasks() {
  await fetch('/api/pocketclaw/mp/tasks/clear_done', { method: 'DELETE' });
  showToast('已清空打勾任务');
  loadOverview();
}

function setPresetWords(type) {
  const presets = {
    '三年级精选': '温暖, 认真, 遵守, 奇迹, 灿烂, 严寒, 准备, 丰富',
    '二年级字词': '春雨, 阳光, 树木, 朋友, 快乐, 仔细, 帮助, 寻找',
    '期中易错词': '仔细, 演变, 聚集, 迅速, 既然, 赞美, 敬佩, 勇敢'
  };
  document.getElementById('dictationInput').value = presets[type] || '';
}

async function saveDictation() {
  const text = document.getElementById('dictationInput').value.trim();
  if (!text) { showToast('请输入听写生词'); return; }
  await fetch('/api/pocketclaw/mp/dictation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_text: text })
  });
  showToast('听写词单已更新并同步到 Note 4！');
  loadOverview();
}

async function setPoemTarget(title) {
  await fetch('/api/pocketclaw/mp/poems/target', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title })
  });
  state.curPoemTitle = title;
  showToast('已设为当前背诵目标：《' + title + '》');
  loadOverview();
  loadPoems();
}

function selectArithPill(type, val) {
  if (type === 'grade') {
    state.arithGrade = val;
    document.querySelectorAll('#arithGradeGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.val == val));
  } else if (type === 'count') {
    state.arithCount = val;
    document.querySelectorAll('#arithCountGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.val == val));
  }
}

async function saveArithConfig() {
  await fetch('/api/pocketclaw/mp/arith', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ grade: state.arithGrade, count: state.arithCount, op_type: 'all' })
  });
  showToast('口算配置已保存！孩子说“做口算”即按此练习。');
}

async function addVocabWord() {
  const w = document.getElementById('newWord').value.trim();
  const m = document.getElementById('newMeaning').value.trim();
  const e = document.getElementById('newExample').value.trim();
  const ec = document.getElementById('newExampleCn').value.trim();
  if (!w) { showToast('请输入单词'); return; }
  await fetch('/api/pocketclaw/mp/vocab', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ word: w, meaning: m, example: e, example_cn: ec })
  });
  document.getElementById('newWord').value = '';
  document.getElementById('newMeaning').value = '';
  document.getElementById('newExample').value = '';
  document.getElementById('newExampleCn').value = '';
  showToast('单词已加入生词本！');
  loadOverview();
}

async function deleteVocab(id) {
  await fetch('/api/pocketclaw/mp/vocab/' + id, { method: 'DELETE' });
  showToast('单词已删除');
  loadOverview();
}

async function setReadingTarget(id) {
  await fetch('/api/pocketclaw/mp/reading/target', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ article_id: id })
  });
  state.curReadingId = id;
  showToast('已更新当前阅读篇目，Note 4 正在同步！');
  loadOverview();
  loadReadingArticles();
}

function setMessageText(msg) {
  document.getElementById('parentMsgInput').value = msg;
}

async function sendParentMessage() {
  const text = document.getElementById('parentMsgInput').value.trim();
  if (!text) { showToast('请输入留言内容'); return; }
  await fetch('/api/pocketclaw/mp/message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: text })
  });
  document.getElementById('parentMsgInput').value = '';
  showToast('💌 留言已成功弹屏推送到 Note 4！');
}

// Init
loadOverview();
loadPoems();
loadReadingArticles();
loadReport();
setInterval(loadOverview, 5000);
</script>
</body>
</html>
"""

print(f"Generated HTML length: {len(portal_html)}")
