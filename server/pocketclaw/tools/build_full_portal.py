# -*- coding: utf-8 -*-
"""Generate the comprehensive 12-tab Parent Web Portal for PocketClaw."""
import re
from pathlib import Path

portal_html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>PocketClaw · 小学生全能学习管理家长控制台</title>
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
    --warning: #f59e0b;
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
  .container { max-width: 720px; margin: 0 auto; }
  header {
    background: var(--card);
    border-radius: 14px;
    padding: 14px 16px;
    margin-bottom: 12px;
    border: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
  }
  .brand { display: flex; align-items: center; gap: 8px; }
  .brand-icon { font-size: 22px; }
  .brand-title { font-size: 16px; font-weight: 700; color: var(--text); }
  .header-badges { display: flex; gap: 6px; align-items: center; }
  .badge {
    padding: 3px 8px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
  }
  .badge-online { background: #dcfce7; color: #15803d; }
  .badge-offline { background: #f1f5f9; color: #64748b; }
  .badge-battery { background: #e0f2fe; color: #0369a1; }

  /* Tabs Navigation - Horizontal scroll friendly for WeChat */
  .tabs-nav {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding-bottom: 8px;
    margin-bottom: 12px;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }
  .tabs-nav::-webkit-scrollbar { display: none; }
  .tab-btn {
    flex-shrink: 0;
    padding: 7px 12px;
    border-radius: 20px;
    border: 1px solid var(--border);
    background: var(--card);
    color: var(--muted);
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .tab-btn.active {
    background: var(--primary);
    color: #fff;
    border-color: var(--primary);
  }

  .tab-content { display: none; }
  .tab-content.active { display: block; }

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

  textarea, input[type="text"], select {
    width: 100%;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--border);
    font-size: 14px;
    outline: none;
    font-family: inherit;
    background: #fff;
  }
  textarea:focus, input[type="text"]:focus, select:focus { border-color: var(--primary); }
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

  .grid-card {
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px;
    cursor: pointer;
    transition: all 0.15s ease;
    background: #fff;
  }
  .grid-card:hover { border-color: var(--primary-border); }
  .grid-card.active { border-color: var(--primary); background: var(--primary-light); }
  .grid-card-title { font-weight: 700; font-size: 14px; margin-bottom: 4px; }
  .grid-card-desc { font-size: 12px; color: var(--muted); }

  .pill-group { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
  .pill-btn {
    padding: 4px 10px;
    border-radius: 16px;
    font-size: 12px;
    border: 1px solid var(--border);
    background: #fff;
    cursor: pointer;
  }
  .pill-btn.active {
    background: var(--primary);
    color: #fff;
    border-color: var(--primary);
  }

  .toast {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(15, 23, 42, 0.9);
    color: #fff;
    padding: 10px 18px;
    border-radius: 20px;
    font-size: 13px;
    display: none;
    z-index: 1000;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  }
  .toast.show { display: block; animation: fadeIn 0.2s; }
  @keyframes fadeIn { from { opacity: 0; transform: translate(-50%, 10px); } to { opacity: 1; transform: translate(-50%, 0); } }

  /* In-page Modal Dialogs for Mobile & WeChat */
  .modal-backdrop {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(15, 23, 42, 0.6);
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 999;
    padding: 16px;
  }
  .modal-backdrop.show {
    display: flex;
    animation: modalBgIn 0.2s ease-out;
  }
  .modal-card {
    background: #ffffff;
    border-radius: 16px;
    padding: 20px;
    width: 100%;
    max-width: 440px;
    box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
    animation: modalSlideUp 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  }
  @keyframes modalBgIn { from { opacity: 0; } to { opacity: 1; } }
  @keyframes modalSlideUp { from { opacity: 0; transform: translateY(20px) scale(0.96); } to { opacity: 1; transform: translateY(0) scale(1); } }
  .modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 14px;
  }
  .modal-title { font-size: 16px; font-weight: 700; color: var(--text); }
  .modal-close {
    background: none; border: none; font-size: 22px; cursor: pointer; color: var(--muted);
    padding: 4px 8px; line-height: 1; border-radius: 6px;
  }
  .modal-close:hover { background: #f1f5f9; }
  .modal-body { display: flex; flex-direction: column; gap: 10px; margin-bottom: 16px; }
  .modal-footer { display: flex; justify-content: flex-end; gap: 10px; }
  .chip-group { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip-btn {
    padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border);
    background: #f8fafc; font-size: 13px; font-weight: 600; cursor: pointer;
    transition: all 0.15s; color: var(--text);
  }
  .chip-btn:hover { background: var(--primary-light); border-color: var(--primary); color: var(--primary); }
  .chip-btn.active { background: var(--primary); color: #fff; border-color: var(--primary); }
</style>
</head>
<body>
<div class="container">
  <header>
    <div class="brand">
      <span class="brand-icon">🎒</span>
      <div>
        <div class="brand-title">PocketClaw · 小学生学习伴侣</div>
        <div style="font-size:11px; color:var(--muted);">Note 4 墨水屏全功能家长控制台</div>
      </div>
    </div>
    <div class="header-badges">
      <span id="badgeOnline" class="badge badge-offline">⚪ 连接中</span>
      <span id="badgeBattery" class="badge badge-battery">🔋 --</span>
    </div>
  </header>

  <!-- Horizontal Tabs -->
  <div class="tabs-nav">
    <button class="tab-btn active" data-tab="tasks" onclick="switchTab('tasks')">📋 作业清单</button>
    <button class="tab-btn" data-tab="habits" onclick="switchTab('habits')">🌟 习惯打卡</button>
    <button class="tab-btn" data-tab="stars" onclick="switchTab('stars')">⭐ 星星存折</button>
    <button class="tab-btn" data-tab="timetable" onclick="switchTab('timetable')">📅 桌面课表</button>
    <button class="tab-btn" data-tab="mistakes" onclick="switchTab('mistakes')">📕 错题专练</button>
    <button class="tab-btn" data-tab="idiom" onclick="switchTab('idiom')">📜 每日成语</button>
    <button class="tab-btn" data-tab="dictation" onclick="switchTab('dictation')">✍️ 生字听写</button>
    <button class="tab-btn" data-tab="poem" onclick="switchTab('poem')">📜 课内古诗</button>
    <button class="tab-btn" data-tab="arith" onclick="switchTab('arith')">🔢 口算闯关</button>
    <button class="tab-btn" data-tab="vocab" onclick="switchTab('vocab')">🔤 英语生词</button>
    <button class="tab-btn" data-tab="reading" onclick="switchTab('reading')">📖 英语短文</button>
    <button class="tab-btn" data-tab="message" onclick="switchTab('message')">💌 亲子留言</button>
    <button class="tab-btn" data-tab="report" onclick="switchTab('report')">📊 学情报表</button>
  </div>

  <!-- Tab 1: 作业清单 -->
  <div id="tab-tasks" class="tab-content active">
    <div class="card">
      <div class="card-title">
        <span>微信班级群作业一键粘贴</span>
        <span class="card-subtitle">智能识别学科与序号</span>
      </div>
      <textarea id="rawTasksInput" placeholder="直接复制老师在微信群发的作业通知，例如：
1. 语文：抄写生字两遍
2. 数学：口算练习20题
3. 英语：大声朗读第一单元课文"></textarea>
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

  <!-- Tab 2: 习惯打卡 -->
  <div id="tab-habits" class="tab-content">
    <div class="card">
      <div class="card-title">
        <span>🌟 自律好习惯打卡 (<span id="habitStats">0/0</span>)</span>
        <span class="badge" style="background:#fef3c7; color:#b45309;" id="habitStarsBadge">⭐ 0 颗星</span>
      </div>
      <p style="font-size:12px; color:var(--muted); margin-bottom:12px;">
        每天打卡积累成长星星，培养自律好习惯。按键打勾实时同步到 Note 4 墨水屏！
      </p>
      <div style="display:flex; gap:8px; margin-bottom:12px;">
        <select id="newHabitIcon" style="width:100px;">
          <option value="📖">📖 晨读</option>
          <option value="🏃">🏃 运动</option>
          <option value="🎒">🎒 整理</option>
          <option value="👀">👀 护眼</option>
          <option value="🛏️">🛏️ 作息</option>
          <option value="⭐">⭐ 习惯</option>
        </select>
        <input type="text" id="newHabitInput" placeholder="输入好习惯名称 (例: 跳绳500个)...">
        <button class="btn btn-primary btn-sm" onclick="addHabit()">添加</button>
      </div>
      <div id="habitList" style="display:flex; flex-direction:column; gap:8px;"></div>
    </div>
  </div>

  <!-- Tab: 星星心愿存折 -->
  <div id="tab-stars" class="tab-content">
    <div class="card" style="background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%); border-color: #fde68a;">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
        <div>
          <div style="font-size:12px; color:#b45309; font-weight:700;">⭐ 自律星星存折可用余额</div>
          <div style="font-size:36px; font-weight:800; color:#b45309; line-height:1.2;" id="starBalanceDisplay">0 ⭐</div>
          <div style="font-size:11px; color:#92400e; margin-top:2px;">
            累计自律获得: <span id="starTotalEarned" style="font-weight:700;">0</span> 颗 · 累计心愿兑换: <span id="starTotalSpent" style="font-weight:700;">0</span> 颗
          </div>
        </div>
        <button class="btn btn-sm" onclick="openAddStarsModal()" style="background:#d97706; color:#fff; border:none; padding:8px 14px; border-radius:8px; font-weight:600; cursor:pointer;">+ 家长表扬奖励</button>
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        <span>🎁 孩子的心愿兑换商场</span>
        <button class="btn btn-secondary btn-sm" onclick="openAddWishModal()">+ 新建心愿</button>
      </div>
      <p style="font-size:12px; color:var(--muted); margin-bottom:12px;">
        星星充足时可帮孩子兑换心愿，兑换成功将向 Note 4 墨水屏推送表扬便签！
      </p>
      <div id="wishCardsGrid" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(200px, 1fr)); gap:10px;"></div>
    </div>

    <div class="card">
      <div class="card-title">📜 星星变动明细</div>
      <div id="starHistoryList" style="display:flex; flex-direction:column; gap:6px; max-height:220px; overflow-y:auto;"></div>
    </div>
  </div>

  <!-- Tab 3: 桌面课表 -->
  <div id="tab-timetable" class="tab-content">
    <div class="card">
      <div class="card-title">
        <span>📅 每周课程表与课前书包备忘</span>
        <span class="badge badge-online">设备同步</span>
      </div>
      <div class="pill-group" id="ttDaysGroup" style="margin-bottom:12px;">
        <button class="pill-btn active" data-day="1" onclick="switchTimetableDay('1')">周一</button>
        <button class="pill-btn" data-day="2" onclick="switchTimetableDay('2')">周二</button>
        <button class="pill-btn" data-day="3" onclick="switchTimetableDay('3')">周三</button>
        <button class="pill-btn" data-day="4" onclick="switchTimetableDay('4')">周四</button>
        <button class="pill-btn" data-day="5" onclick="switchTimetableDay('5')">周五</button>
      </div>
      <div style="font-size:12px; font-weight:600; margin-bottom:6px;">课程安排（第1至第6节）：</div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:12px;" id="ttInputsGrid">
        <input type="text" id="ttC1" placeholder="第1节">
        <input type="text" id="ttC2" placeholder="第2节">
        <input type="text" id="ttC3" placeholder="第3节">
        <input type="text" id="ttC4" placeholder="第4节">
        <input type="text" id="ttC5" placeholder="第5节">
        <input type="text" id="ttC6" placeholder="第6节">
      </div>
      <div style="margin-bottom:12px;">
        <label style="font-size:12px; font-weight:600; color:var(--text); display:block; margin-bottom:4px;">🎒 课前备忘/学具提醒：</label>
        <input type="text" id="ttNoteInput" placeholder="例如: 穿礼服戴红领巾升旗、带科学实验盒">
      </div>
      <button class="btn btn-primary btn-block" onclick="saveCurrentTimetableDay()">💾 保存当日课表并同步到墨水屏</button>
    </div>
  </div>

  <!-- Tab 4: 错题专练 -->
  <div id="tab-mistakes" class="tab-content">
    <div class="card">
      <div class="card-title">
        <span>📕 错题强化消灭本 (<span id="mistakeCount">0</span>)</span>
        <button class="btn btn-secondary btn-sm" onclick="clearAllMistakes()">清空所有错题</button>
      </div>
      <p style="font-size:12px; color:var(--muted); margin-bottom:12px;">
        口算做错算式、听写生字与英语跟读错题自动沉淀在此，孩子可在 Note 4 上专项消灭！
      </p>
      <div id="mistakeList" style="display:flex; flex-direction:column; gap:8px;"></div>
    </div>
  </div>

  <!-- Tab 5: 每日成语 -->
  <div id="tab-idiom" class="tab-content">
    <div class="card">
      <div class="card-title">
        <span>📜 今日推荐国学成语</span>
        <button class="btn btn-primary btn-sm" onclick="pushIdiomToDevice()">🚀 推送到墨水屏</button>
      </div>
      <div style="background:#f8fafc; border:1px solid var(--border); border-radius:10px; padding:12px; margin-bottom:12px;">
        <div style="display:flex; align-items:baseline; gap:8px;">
          <span style="font-size:18px; font-weight:700; color:var(--primary);" id="curIdiomWord">闻鸡起舞</span>
          <span style="font-size:13px; color:var(--muted);" id="curIdiomPinyin">wén jī qǐ wǔ</span>
        </div>
        <div style="font-size:13px; color:var(--text); margin-top:6px;" id="curIdiomMeaning"></div>
        <div style="font-size:12px; color:var(--muted); margin-top:4px;" id="curIdiomSource"></div>
        <div style="font-size:12px; color:var(--tag-chinese); margin-top:4px;" id="curIdiomExample"></div>
      </div>
      <div class="card-title" style="margin-bottom:8px;"><span>国学成语库（点击一键设为今日成语）</span></div>
      <div id="idiomLibraryList" style="display:flex; flex-direction:column; gap:8px;"></div>
    </div>
  </div>

  <!-- Tab 6: 生字听写 -->
  <div id="tab-dictation" class="tab-content">
    <div class="card">
      <div class="card-title">
        <span>语文生字词听写设置</span>
        <span class="card-subtitle">已设置 <span id="dictCount">0</span> 词</span>
      </div>
      <div class="pill-group">
        <button class="pill-btn" onclick="setPresetWords('三年级精选')">三年级精选</button>
        <button class="pill-btn" onclick="setPresetWords('二年级字词')">二年级字词</button>
        <button class="pill-btn" onclick="setPresetWords('期中易错词')">期中易错词</button>
      </div>
      <textarea id="dictationInput" placeholder="输入生字或词语，用逗号或换行隔开，例如：
温暖, 认真, 遵守, 奇迹, 灿烂, 丰富"></textarea>
      <div class="btn-row">
        <button class="btn btn-primary btn-block" onclick="saveDictation()">💾 保存听写词单并推屏</button>
      </div>
    </div>
    <div class="card">
      <div class="card-title"><span>当前听写词单预览</span></div>
      <div id="dictationPreview" style="font-size:13px; color:var(--muted); line-height:1.8;"></div>
    </div>
  </div>

  <!-- Tab 7: 课内古诗 -->
  <div id="tab-poem" class="tab-content">
    <div class="card">
      <div class="card-title">
        <span>部编版小学必背古诗词</span>
        <span class="badge badge-online" id="curPoemBadge">当前目标：静夜思</span>
      </div>
      <div id="poemGrid" style="display:grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap:10px;"></div>
    </div>
  </div>

  <!-- Tab 8: 口算闯关 -->
  <div id="tab-arith" class="tab-content">
    <div class="card">
      <div class="card-title"><span>口算难度与题量配置</span></div>
      <div style="margin-bottom:12px;">
        <label style="font-size:13px; font-weight:600; display:block; margin-bottom:6px;">年级难度：</label>
        <div class="pill-group" id="arithGradeGroup">
          <button class="pill-btn" data-val="1" onclick="selectArithPill('grade', 1)">一年级 (20以内加减)</button>
          <button class="pill-btn active" data-val="2" onclick="selectArithPill('grade', 2)">二年级 (100以内表内乘除)</button>
          <button class="pill-btn" data-val="3" onclick="selectArithPill('grade', 3)">三年级 (两位数乘一位数/三位加减)</button>
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

  <!-- Tab 9: 英语生词 -->
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

  <!-- Tab 10: 英语短文 -->
  <div id="tab-reading" class="tab-content">
    <div class="card">
      <div class="card-title">
        <span>小学英语分级阅读篇目</span>
        <span class="badge badge-online" id="curReadingBadge">当前篇目</span>
      </div>
      <p style="font-size:12px; color:var(--muted); margin-bottom:12px;">
        墨水屏完整展示短文，按▲/▼键选句时黑底白字高亮，自动英音朗读，长按跟读评测。
      </p>
      <div id="readingArticlesList" style="display:flex; flex-direction:column; gap:10px;"></div>
    </div>
  </div>

  <!-- Tab 11: 亲子留言 -->
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

  <!-- Tab 12: 学情报表 -->
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

<!-- Modal 1: 奖励星星 (替代移动端受限的 window.prompt) -->
<div id="modalAddStars" class="modal-backdrop" onclick="if(event.target===this)closeAddStarsModal()">
  <div class="modal-card">
    <div class="modal-header">
      <div class="modal-title">⭐ 奖励自律小红星</div>
      <button class="modal-close" onclick="closeAddStarsModal()">&times;</button>
    </div>
    <div class="modal-body">
      <label style="font-size:13px; font-weight:600; color:var(--text);">快速选择奖励数量：</label>
      <div class="chip-group">
        <button class="chip-btn active" type="button" onclick="selectStarCount(1, this)">+1 星</button>
        <button class="chip-btn" type="button" onclick="selectStarCount(2, this)">+2 星</button>
        <button class="chip-btn" type="button" onclick="selectStarCount(5, this)">+5 星</button>
        <button class="chip-btn" type="button" onclick="selectStarCount(10, this)">+10 星</button>
        <button class="chip-btn" type="button" onclick="selectStarCount(20, this)">+20 星</button>
      </div>
      <input type="number" id="inputStarCount" class="input" value="1" min="1" max="100" placeholder="星星数量">
      <label style="font-size:13px; font-weight:600; color:var(--text); margin-top:4px;">奖励表扬原因：</label>
      <div class="chip-group">
        <button class="chip-btn active" type="button" onclick="selectStarReason('主动整理书桌', this)">整理书桌</button>
        <button class="chip-btn" type="button" onclick="selectStarReason('作业工整专注', this)">作业专注</button>
        <button class="chip-btn" type="button" onclick="selectStarReason('认真早读背诵', this)">早读背诵</button>
        <button class="chip-btn" type="button" onclick="selectStarReason('早起按时洗漱', this)">自律早起</button>
        <button class="chip-btn" type="button" onclick="selectStarReason('主动分担家务', this)">分担家务</button>
      </div>
      <input type="text" id="inputStarReason" class="input" value="主动整理书桌" placeholder="输入表扬原因">
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeAddStarsModal()">取消</button>
      <button class="btn" style="background:#d97706; color:#fff;" onclick="confirmAddStars()">发放星星并推屏 ⭐</button>
    </div>
  </div>
</div>

<!-- Modal 2: 新建心愿奖品 (替代移动端受限的 window.prompt) -->
<div id="modalAddWish" class="modal-backdrop" onclick="if(event.target===this)closeAddWishModal()">
  <div class="modal-card">
    <div class="modal-header">
      <div class="modal-title">🎁 上架兑换心愿奖品</div>
      <button class="modal-close" onclick="closeAddWishModal()">&times;</button>
    </div>
    <div class="modal-body">
      <label style="font-size:13px; font-weight:600; color:var(--text);">心愿奖品名称：</label>
      <input type="text" id="inputWishTitle" class="input" placeholder="例: 去一次科技馆 / 看一场电影 / 买拼装乐高">
      <label style="font-size:13px; font-weight:600; color:var(--text); margin-top:4px;">所需星星数：</label>
      <div class="chip-group">
        <button class="chip-btn" type="button" onclick="selectWishStars(10, this)">10 星</button>
        <button class="chip-btn active" type="button" onclick="selectWishStars(20, this)">20 星</button>
        <button class="chip-btn" type="button" onclick="selectWishStars(30, this)">30 星</button>
        <button class="chip-btn" type="button" onclick="selectWishStars(50, this)">50 星</button>
        <button class="chip-btn" type="button" onclick="selectWishStars(100, this)">100 星</button>
      </div>
      <input type="number" id="inputWishStars" class="input" value="20" min="1" max="1000" placeholder="所需星星数">
      <label style="font-size:13px; font-weight:600; color:var(--text); margin-top:4px;">心愿图标 Emoji：</label>
      <div class="chip-group" id="wishEmojiGroup">
        <button class="chip-btn active" type="button" onclick="selectWishEmoji('🎁', this)">🎁</button>
        <button class="chip-btn" type="button" onclick="selectWishEmoji('🚀', this)">🚀</button>
        <button class="chip-btn" type="button" onclick="selectWishEmoji('🍦', this)">🍦</button>
        <button class="chip-btn" type="button" onclick="selectWishEmoji('🎮', this)">🎮</button>
        <button class="chip-btn" type="button" onclick="selectWishEmoji('📚', this)">📚</button>
        <button class="chip-btn" type="button" onclick="selectWishEmoji('🎡', this)">🎡</button>
        <button class="chip-btn" type="button" onclick="selectWishEmoji('🚴', this)">🚴</button>
        <button class="chip-btn" type="button" onclick="selectWishEmoji('🎨', this)">🎨</button>
        <button class="chip-btn" type="button" onclick="selectWishEmoji('🍿', this)">🍿</button>
        <button class="chip-btn" type="button" onclick="selectWishEmoji('🧸', this)">🧸</button>
      </div>
      <input type="hidden" id="inputWishIcon" value="🎁">
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeAddWishModal()">取消</button>
      <button class="btn" onclick="confirmAddWish()">上架心愿奖品 🎁</button>
    </div>
  </div>
</div>

<div id="toast" class="toast"></div>

<script>
let state = {
  currentTab: 'tasks',
  arithGrade: 2,
  arithCount: 10,
  curPoemTitle: '',
  curReadingId: '',
  curDay: '1',
  timetableAll: {},
  curIdiom: {},
  allIdioms: []
};

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}

function switchTab(name) {
  state.currentTab = name;
  document.querySelectorAll('.tab-btn').forEach(b => {
    if (b.getAttribute('data-tab') === name) {
      b.classList.add('active');
      if (b.scrollIntoView) {
        b.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
      }
    } else {
      b.classList.remove('active');
    }
  });
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
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

    // Habits tab
    if (data.habits) {
      document.getElementById('habitStats').textContent = data.habit_stats.done + '/' + data.habit_stats.total;
      document.getElementById('habitStarsBadge').textContent = '⭐ ' + data.habit_stats.stars + ' 颗星';
      renderHabits(data.habits);
    }

    // Stars tab
    if (data.star_bank) {
      renderStars(data.star_bank);
    }

    // Timetable tab
    if (data.timetable_all) {
      state.timetableAll = data.timetable_all;
      fillTimetableDay(state.curDay);
    }

    // Mistakes tab
    if (data.mistakes) {
      document.getElementById('mistakeCount').textContent = data.mistakes_count;
      renderMistakes(data.mistakes);
    }

    // Idiom tab
    if (data.daily_idiom) {
      state.curIdiom = data.daily_idiom;
      renderDailyIdiomCard(data.daily_idiom);
    }

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
    <div class="task-item" data-id="${t.id}">
      <div class="task-label" onclick="toggleTask('${t.id}')">
        <input type="checkbox" ${t.done ? 'checked' : ''} style="cursor:pointer; width:16px; height:16px;">
        <span class="subject-pill sub-${t.subject || '通用'}">${t.subject || '通用'}</span>
        <span class="task-text ${t.done ? 'done' : ''}">${t.text}</span>
      </div>
      <button class="btn btn-secondary btn-sm" onclick="deleteTask('${t.id}')" style="padding:2px 6px;">✕</button>
    </div>
  `).join('');
}

function renderHabits(habits) {
  const list = document.getElementById('habitList');
  if (!habits || habits.length === 0) {
    list.innerHTML = '<div style="color:var(--muted); font-size:13px; text-align:center; padding:16px;">暂无习惯项，可点击上方添加～</div>';
    return;
  }
  list.innerHTML = habits.map(h => `
    <div class="task-item" data-id="${h.id}">
      <div class="task-label" onclick="toggleHabit('${h.id}')">
        <input type="checkbox" ${h.done ? 'checked' : ''} style="cursor:pointer; width:16px; height:16px;">
        <span style="font-size:16px;">${h.icon || '⭐'}</span>
        <span class="task-text ${h.done ? 'done' : ''}" style="font-weight:600;">${h.name}</span>
        ${h.streak > 1 ? `<span class="badge" style="background:#fee2e2; color:#dc2626; font-size:10px;">🔥 连打${h.streak}天</span>` : ''}
      </div>
      <button class="btn btn-secondary btn-sm" onclick="deleteHabit('${h.id}')" style="padding:2px 6px;">✕</button>
    </div>
  `).join('');
}

function switchTimetableDay(day) {
  state.curDay = day;
  document.querySelectorAll('#ttDaysGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.day === day));
  fillTimetableDay(day);
}

function fillTimetableDay(day) {
  const d = state.timetableAll[day] || { courses: ['', '', '', '', '', ''], note: '' };
  const courses = d.courses || [];
  for (let i = 1; i <= 6; i++) {
    const el = document.getElementById('ttC' + i);
    if (el) el.value = courses[i - 1] || '';
  }
  const noteEl = document.getElementById('ttNoteInput');
  if (noteEl) noteEl.value = d.note || '';
}

function renderMistakes(mistakes) {
  const el = document.getElementById('mistakeList');
  if (!mistakes || mistakes.length === 0) {
    el.innerHTML = '<div style="color:var(--success); font-size:13px; text-align:center; padding:16px; background:#f0fdf4; border-radius:10px;">🎉 太棒啦！当前没有历史错题！</div>';
    return;
  }
  const catNames = { arith: '口算速练', dictation: '生字听写', reading: '英语阅读' };
  el.innerHTML = mistakes.map(m => `
    <div style="border:1px solid var(--border); border-radius:10px; padding:10px; display:flex; justify-content:space-between; align-items:center;">
      <div>
        <div style="font-size:11px; color:var(--muted); margin-bottom:2px;">
          <span class="badge" style="background:#f1f5f9;">${catNames[m.category] || '学科错题'}</span>
          ${m.count > 1 ? `<span class="badge" style="background:#fee2e2; color:#dc2626;">复错 ${m.count} 次</span>` : ''}
        </div>
        <div style="font-weight:700; font-size:14px; color:var(--text);">${m.question}</div>
        <div style="font-size:12px; color:var(--success); margin-top:2px;">正确答案: ${m.expected} ${m.wrong_answer ? `<span style="color:#dc2626; margin-left:8px;">历史错答: ${m.wrong_answer}</span>` : ''}</div>
      </div>
      <button class="btn btn-secondary btn-sm" onclick="eliminateMistake('${m.id}')">✓ 已消灭</button>
    </div>
  `).join('');
}

function renderDailyIdiomCard(idiom) {
  document.getElementById('curIdiomWord').textContent = idiom.idiom || '';
  document.getElementById('curIdiomPinyin').textContent = idiom.pinyin ? '(' + idiom.pinyin + ')' : '';
  document.getElementById('curIdiomMeaning').textContent = '【释义】' + (idiom.meaning || '');
  document.getElementById('curIdiomSource').textContent = idiom.source ? '【出处】' + idiom.source : '';
  document.getElementById('curIdiomExample').textContent = idiom.example ? '【造句】' + idiom.example : '';
}

async function loadIdioms() {
  const res = await fetch('/api/pocketclaw/mp/idiom');
  const data = await res.json();
  if (!data.ok) return;
  state.allIdioms = data.all;
  const list = document.getElementById('idiomLibraryList');
  list.innerHTML = data.all.map(item => `
    <div class="grid-card ${item.idiom === state.curIdiom.idiom ? 'active' : ''}" onclick='selectIdiom(${JSON.stringify(item)})'>
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <span class="grid-card-title">${item.idiom}</span>
          <span style="font-size:11px; color:var(--muted); margin-left:6px;">(${item.pinyin})</span>
        </div>
        <button class="btn ${item.idiom === state.curIdiom.idiom ? 'btn-primary' : 'btn-secondary'} btn-sm">
          ${item.idiom === state.curIdiom.idiom ? '✓ 推荐中' : '设为今日成语'}
        </button>
      </div>
      <div class="grid-card-desc" style="margin-top:4px;">${item.meaning}</div>
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
  const item = document.querySelector(`#taskList .task-item[data-id="${id}"]`);
  if (item) {
    const cb = item.querySelector('input[type="checkbox"]');
    const txt = item.querySelector('.task-text');
    if (cb) cb.checked = !cb.checked;
    if (txt) txt.classList.toggle('done');
  }
  try {
    const res = await fetch('/api/pocketclaw/mp/tasks/' + id + '/toggle', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error('toggle failed');
    loadOverview();
  } catch (e) {
    showToast('网络请求失败，请重试');
    loadOverview();
  }
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

async function addHabit() {
  const inp = document.getElementById('newHabitInput');
  const icon = document.getElementById('newHabitIcon').value;
  const name = inp.value.trim();
  if (!name) return;
  await fetch('/api/pocketclaw/mp/habits', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name, icon: icon })
  });
  inp.value = '';
  showToast('好习惯已添加！');
  loadOverview();
}

async function toggleHabit(id) {
  const item = document.querySelector(`#habitList .task-item[data-id="${id}"]`);
  if (item) {
    const cb = item.querySelector('input[type="checkbox"]');
    const txt = item.querySelector('.task-text');
    if (cb) cb.checked = !cb.checked;
    if (txt) txt.classList.toggle('done');
  }
  try {
    const res = await fetch('/api/pocketclaw/mp/habits/' + id + '/toggle', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error('toggle failed');
    loadOverview();
  } catch (e) {
    showToast('网络请求失败，请重试');
    loadOverview();
  }
}

async function deleteHabit(id) {
  await fetch('/api/pocketclaw/mp/habits/' + id, { method: 'DELETE' });
  loadOverview();
}

function renderStars(bank) {
  if (!bank) return;
  state.starBank = bank;
  const balEl = document.getElementById('starBalanceDisplay');
  if (balEl) balEl.textContent = bank.balance + ' ⭐';
  const earnEl = document.getElementById('starTotalEarned');
  if (earnEl) earnEl.textContent = bank.total_earned;
  const spentEl = document.getElementById('starTotalSpent');
  if (spentEl) spentEl.textContent = bank.spent;

  const grid = document.getElementById('wishCardsGrid');
  if (!grid) return;
  grid.innerHTML = '';
  (bank.wishes || []).forEach(w => {
    const card = document.createElement('div');
    const canRedeem = (bank.balance >= w.stars_needed && !w.redeemed);
    const progress = Math.min(100, Math.round((bank.balance / w.stars_needed) * 100));
    card.style = 'border:1px solid var(--border); border-radius:10px; padding:10px; background:' + (w.redeemed ? '#f8fafc' : '#ffffff') + '; display:flex; flex-direction:column; justify-content:space-between; gap:6px; box-shadow:0 1px 2px rgba(0,0,0,0.02);';
    card.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <span style="font-size:22px;">${w.icon || '🎁'}</span>
        <button onclick="deleteWish('${w.id}')" style="background:none; border:none; color:#94a3b8; font-size:14px; cursor:pointer;" title="删除心愿">✕</button>
      </div>
      <div style="font-size:13px; font-weight:700; color:var(--text);">${w.title}</div>
      <div style="font-size:11px; color:#b45309; font-weight:600;">需 ${w.stars_needed} 颗星星</div>
      ${w.redeemed ? `
        <div style="font-size:11px; color:var(--success); font-weight:700; padding:4px 0; text-align:center; background:#dcfce7; border-radius:6px;">✓ 已成功兑换</div>
      ` : `
        <div style="background:#f1f5f9; border-radius:4px; height:6px; overflow:hidden; margin:2px 0;">
          <div style="background:#f59e0b; width:${progress}%; height:100%;"></div>
        </div>
        <button class="btn btn-sm ${canRedeem ? 'btn-primary' : 'btn-secondary'}" style="width:100%; font-size:11px; padding:6px 0;" onclick="redeemWish('${w.id}')" ${canRedeem ? '' : 'disabled'}>
          ${canRedeem ? '✨ 帮孩子兑换' : '还差 ' + (w.stars_needed - bank.balance) + ' 颗星'}
        </button>
      `}
    `;
    grid.appendChild(card);
  });

  const hList = document.getElementById('starHistoryList');
  if (!hList) return;
  hList.innerHTML = '';
  const hist = (bank.history || []).slice().reverse();
  if (hist.length === 0) {
    hList.innerHTML = '<div style="font-size:12px; color:var(--muted); text-align:center; padding:10px;">暂无明细</div>';
  } else {
    hist.forEach(h => {
      const row = document.createElement('div');
      const isPlus = h.delta > 0;
      const tStr = new Date(h.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      row.style = 'display:flex; justify-content:space-between; align-items:center; padding:6px 8px; border-radius:6px; background:#f8fafc; font-size:12px;';
      row.innerHTML = `
        <div style="display:flex; gap:8px; align-items:center;">
          <span style="color:var(--muted); font-size:11px;">${tStr}</span>
          <span style="font-weight:600; color:var(--text);">${h.reason || '自律奖励'}</span>
        </div>
        <span style="font-weight:700; color:${isPlus ? '#16a34a' : '#dc2626'};">${isPlus ? '+' : ''}${h.delta} ⭐</span>
      `;
      hList.appendChild(row);
    });
  }
}

async function redeemWish(wishId) {
  if (!confirm('确定要帮孩子兑换该心愿吗？兑换将扣除对应星星，并向 Note 4 墨水屏发送表扬便签！')) return;
  const res = await fetch('/api/pocketclaw/mp/stars/wishes/' + wishId + '/redeem', { method: 'POST' });
  const data = await res.json();
  if (data.ok) {
    showToast('兑换成功！已向 Note 4 墨水屏推送表扬便签！');
    loadOverview();
  } else {
    showToast(data.error || '兑换失败');
  }
}

async function deleteWish(wishId) {
  if (!confirm('确定要删除该心愿吗？')) return;
  await fetch('/api/pocketclaw/mp/stars/wishes/' + wishId, { method: 'DELETE' });
  showToast('心愿已删除');
  loadOverview();
}

function openAddStarsModal() {
  document.getElementById('modalAddStars').classList.add('show');
}
function closeAddStarsModal() {
  document.getElementById('modalAddStars').classList.remove('show');
}
function selectStarCount(n, btn) {
  document.getElementById('inputStarCount').value = n;
  btn.parentElement.querySelectorAll('.chip-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}
function selectStarReason(r, btn) {
  document.getElementById('inputStarReason').value = r;
  btn.parentElement.querySelectorAll('.chip-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}
async function confirmAddStars() {
  const count = parseInt(document.getElementById('inputStarCount').value);
  if (isNaN(count) || count <= 0) {
    showToast('请输入有效的星星数量');
    return;
  }
  const reason = document.getElementById('inputStarReason').value.trim() || '认真自律表扬';
  try {
    const res = await fetch('/api/pocketclaw/mp/stars/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ count: count, reason: reason })
    });
    const data = await res.json();
    if (data.ok) {
      closeAddStarsModal();
      showToast('已奖励 ' + count + ' 颗星星并即时推送到墨水屏！');
      loadOverview();
    } else {
      showToast('奖励失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    showToast('网络请求失败，请重试');
  }
}

function openAddWishModal() {
  document.getElementById('modalAddWish').classList.add('show');
}
function closeAddWishModal() {
  document.getElementById('modalAddWish').classList.remove('show');
}
function selectWishStars(n, btn) {
  document.getElementById('inputWishStars').value = n;
  btn.parentElement.querySelectorAll('.chip-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}
function selectWishEmoji(e, btn) {
  document.getElementById('inputWishIcon').value = e;
  btn.parentElement.querySelectorAll('.chip-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}
async function confirmAddWish() {
  const title = document.getElementById('inputWishTitle').value.trim();
  if (!title) {
    showToast('请输入心愿名称');
    return;
  }
  const stars = parseInt(document.getElementById('inputWishStars').value) || 20;
  const icon = document.getElementById('inputWishIcon').value || '🎁';
  try {
    const res = await fetch('/api/pocketclaw/mp/stars/wishes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title, stars_needed: stars, icon: icon })
    });
    const data = await res.json();
    if (data.ok) {
      closeAddWishModal();
      document.getElementById('inputWishTitle').value = '';
      showToast('心愿奖品已成功上架！');
      loadOverview();
    } else {
      showToast('上架失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    showToast('网络请求失败，请重试');
  }
}

async function saveCurrentTimetableDay() {
  const courses = [];
  for (let i = 1; i <= 6; i++) {
    courses.push(document.getElementById('ttC' + i).value.trim());
  }
  const note = document.getElementById('ttNoteInput').value.trim();
  state.timetableAll[state.curDay] = {
    day_name: ['周一', '周二', '周三', '周四', '周五'][parseInt(state.curDay) - 1],
    courses: courses,
    note: note
  };
  await fetch('/api/pocketclaw/mp/timetable', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ timetable: state.timetableAll })
  });
  showToast('课表与书包备忘已保存并同步 Note 4！');
  loadOverview();
}

async function eliminateMistake(id) {
  await fetch('/api/pocketclaw/mp/mistakes/' + id, { method: 'DELETE' });
  showToast('错题已成功消灭！');
  loadOverview();
}

async function clearAllMistakes() {
  await fetch('/api/pocketclaw/mp/mistakes', { method: 'DELETE' });
  showToast('错题本已全部清空！');
  loadOverview();
}

async function selectIdiom(item) {
  await fetch('/api/pocketclaw/mp/idiom/target', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(item)
  });
  state.curIdiom = item;
  showToast('已设为今日成语：《' + item.idiom + '》并同步墨水屏！');
  loadOverview();
  loadIdioms();
}

function pushIdiomToDevice() {
  if (state.curIdiom && state.curIdiom.idiom) {
    selectIdiom(state.curIdiom);
  }
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

function setMessageText(t) {
  document.getElementById('parentMsgInput').value = t;
}

async function sendParentMessage() {
  const text = document.getElementById('parentMsgInput').value.trim();
  if (!text) { showToast('请先输入留言内容'); return; }
  const res = await fetch('/api/pocketclaw/mp/message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: text })
  });
  const data = await res.json();
  if (data.ok) {
    document.getElementById('parentMsgInput').value = '';
    showToast('留言已推送并即时展现在墨水屏！');
  }
}

// Initial load
window.addEventListener('DOMContentLoaded', () => {
  loadOverview();
  loadPoems();
  loadReadingArticles();
  loadReport();
  loadIdioms();
  setInterval(loadOverview, 10000);
});
</script>
</body>
</html>"""

def apply_to_endpoint():
    endpoint_path = Path(__file__).resolve().parent.parent / "endpoint.py"
    content = endpoint_path.read_text(encoding="utf-8")
    pattern = r'_HTML_PARENT_PORTAL = """.*?"""\n\n\nclass _Caller:'
    replacement = f'_HTML_PARENT_PORTAL = """{portal_html}"""\n\n\nclass _Caller:'
    new_content, count = re.subn(pattern, replacement, content, count=1, flags=re.DOTALL)
    if count != 1:
        print(f"Failed to replace _HTML_PARENT_PORTAL, count={count}")
        return False
    endpoint_path.write_text(new_content, encoding="utf-8")
    print("Successfully replaced _HTML_PARENT_PORTAL in endpoint.py!")
    return True

if __name__ == "__main__":
    apply_to_endpoint()
