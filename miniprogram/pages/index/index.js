// pages/index/index.js
const api = require('../../utils/api.js');

function formatMMSS(sec) {
  const s = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return (m < 10 ? '0' + m : '' + m) + ':' + (r < 10 ? '0' + r : '' + r);
}

Page({
  openContentEntry() { wx.navigateTo({ url: '/pages/content_entry/content_entry' }); },
  openPresets() { wx.navigateTo({ url: '/pages/presets/presets' }); },
  openPhotoText() { wx.navigateTo({ url: '/pages/photo_text/photo_text' }); },
  data: {
    baseUrl: 'http://127.0.0.1:8000',
    screenImage: '',
    screenCapturedAt: '',
    screenLoading: false,
    online: false,
    battery: null,
    currentModeName: '📋 今日作业',
    starBalance: 0,
    taskStats: { total: 0, done: 0 },
    habitStats: { total: 0, done: 0, stars: 0 },
    mistakesCount: 0,
    pendingDictationCount: 0,
    focusMin: 0,
    focusMinutes: 25,
    focusStyle: 'spark',
    focusStatus: { active: false, paused: false, remaining_s: 0, total_s: 1500, style: 'spark' },
    focusTimeFormatted: '25:00',
    artList: [],
    timetableToday: { day_name: '', courses: [], note: '' },
    dailyIdiom: { idiom: '闻鸡起舞', pinyin: 'wén jī qǐ wǔ', meaning: '比喻有志气的人发奋图强。' },
    parentMsg: ''
  },

  _timer: null,

  onShow() {
    this.setData({ baseUrl: api.getBaseUrl() });
    this.loadData();
    this._startTick();
  },

  onHide() {
    this._stopTick();
  },

  onUnload() {
    this._stopTick();
  },

  _startTick() {
    this._stopTick();
    this._timer = setInterval(() => {
      const fs = this.data.focusStatus;
      if (fs && fs.active && !fs.paused && fs.remaining_s > 0) {
        const nextRem = Math.max(0, fs.remaining_s - 1);
        this.setData({
          'focusStatus.remaining_s': nextRem,
          focusTimeFormatted: formatMMSS(nextRem)
        });
      }
    }, 1000);
  },

  _stopTick() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  },

  onPullDownRefresh() {
    this.loadData().then(() => wx.stopPullDownRefresh());
  },

  async loadData() {
    try {
      const res = await api.getOverview();
      if (res.ok) {
        const fs = res.focus_status || { active: false, paused: false, remaining_s: 0, total_s: 1500, style: 'spark' };
        const timeStr = fs.active ? formatMMSS(fs.remaining_s) : formatMMSS(this.data.focusMinutes * 60);
        this.setData({
          baseUrl: api.getBaseUrl(),
          online: res.online,
          battery: res.battery,
          currentModeName: res.current_mode_name || '📋 今日作业',
          starBalance: (res.star_bank && res.star_bank.balance) || 0,
          taskStats: res.task_stats || { total: 0, done: 0 },
          habitStats: res.habit_stats || { total: 0, done: 0, stars: 0 },
          mistakesCount: res.mistakes_count || 0,
          pendingDictationCount: res.pending_dictation_count || 0,
          focusMin: res.focus_today_min || 0,
          focusStatus: fs,
          focusTimeFormatted: timeStr,
          artList: res.art_list || [],
          timetableToday: res.timetable_today || { day_name: '', courses: [], note: '' },
          dailyIdiom: res.daily_idiom || { idiom: '闻鸡起舞', pinyin: '', meaning: '' }
        });
      }
    } catch (err) {
      console.error('loadOverview error:', err);
    }
  },

  async captureScreen() {
    if (this.data.screenLoading) return;
    if (!wx.getStorageSync('screen_admin_token') && !wx.getStorageSync('family_session_token')) {
      wx.showToast({ title: '请先在空间管理中设置屏幕访问令牌', icon: 'none' });
      return;
    }
    this.setData({ screenLoading: true });
    try {
      const result = await api.captureScreen();
      if (result.ok) this.setData({ screenImage: 'data:image/png;base64,' + result.image,
        screenCapturedAt: new Date(result.captured_at * 1000).toLocaleTimeString() });
    } catch (error) {
      const message = error.data && error.data.detail;
      wx.showToast({ title: message || '采集失败，请重试', icon: 'none' });
    } finally { this.setData({ screenLoading: false }); }
  },

  async quickSwitchMode(e) {
    const mode = e.currentTarget.dataset.mode;
    try {
      const res = await api.switchDeviceMode(mode);
      if (res.ok) {
        wx.showToast({ title: '已切换至 ' + (res.mode_name || mode), icon: 'none' });
        this.loadData();
      }
    } catch (err) {
      wx.showToast({ title: '切屏失败', icon: 'none' });
    }
  },

  async quickPushArt() {
    try {
      const res = await api.pushScreensaver({ mode: 'art', speak: false });
      if (res.ok) {
        wx.showToast({ title: '已推屏16灰阶世界名画', icon: 'success' });
        this.loadData();
      }
    } catch (err) {
      wx.showToast({ title: '推屏失败', icon: 'none' });
    }
  },

  async pushArtItem(e) {
    const { id, title } = e.currentTarget.dataset;
    try {
      const res = await api.pushScreensaver({ mode: 'art', art_id: id, speak: false });
      if (res.ok) {
        wx.showToast({ title: `已推屏《${title}》`, icon: 'success' });
        this.loadData();
      }
    } catch (err) {
      wx.showToast({ title: '推屏失败', icon: 'none' });
    }
  },

  setFocusMin(e) {
    const min = parseInt(e.currentTarget.dataset.min, 10) || 25;
    this.setData({
      focusMinutes: min,
      focusTimeFormatted: this.data.focusStatus.active ? this.data.focusTimeFormatted : formatMMSS(min * 60)
    });
  },

  setFocusStyle(e) {
    this.setData({ focusStyle: e.currentTarget.dataset.style });
  },

  async startFocus() {
    try {
      const res = await api.controlFocus('start', this.data.focusMinutes, this.data.focusStyle, 'pomodoro');
      if (res.ok) {
        wx.showToast({ title: '已开启 Note 4 番茄钟', icon: 'success' });
        this.loadData();
      }
    } catch (err) {
      wx.showToast({ title: '开启失败', icon: 'none' });
    }
  },

  async toggleFocusPause() {
    const action = this.data.focusStatus.paused ? 'resume' : 'pause';
    try {
      const res = await api.controlFocus(action, this.data.focusMinutes, this.data.focusStyle);
      if (res.ok) {
        wx.showToast({ title: action === 'pause' ? '已暂停专注' : '已继续专注', icon: 'none' });
        this.loadData();
      }
    } catch (err) {
      wx.showToast({ title: '操作失败', icon: 'none' });
    }
  },

  async stopFocus() {
    try {
      const res = await api.controlFocus('cancel', this.data.focusMinutes, this.data.focusStyle);
      if (res.ok) {
        wx.showToast({ title: '本轮专注已结束', icon: 'none' });
        this.loadData();
      }
    } catch (err) {
      wx.showToast({ title: '操作失败', icon: 'none' });
    }
  },

  goToTasks() {
    wx.navigateTo({ url: '/pages/tasks/tasks' });
  },

  goToHabits() {
    wx.navigateTo({ url: '/pages/habits/habits' });
  },

  goToTimetable() {
    wx.navigateTo({ url: '/pages/timetable/timetable' });
  },

  goToMistakes() {
    wx.navigateTo({ url: '/pages/mistakes/mistakes' });
  },

  goToReport() {
    wx.switchTab({ url: '/pages/report/report' });
  },

  goToPractice(e) {
    const tab = e.currentTarget.dataset.tab;
    if (['screensaver', 'idiom', 'menu'].includes(tab)) {
      wx.switchTab({ url: '/pages/wisdom/wisdom' });
      return;
    }
    getApp().globalData.practiceTab = tab || 'dictation';
    wx.switchTab({ url: '/pages/practice/practice' });
  },

  setMsg(e) {
    this.setData({ parentMsg: e.currentTarget.dataset.text });
  },

  onMsgInput(e) {
    this.setData({ parentMsg: e.detail.value });
  },

  async sendMessage() {
    const text = this.data.parentMsg.trim();
    if (!text) {
      wx.showToast({ title: '请输入留言内容', icon: 'none' });
      return;
    }
    try {
      const res = await api.sendMessage(text);
      if (res.ok) {
        wx.showToast({ title: '已推送到 Note 4', icon: 'success' });
        this.setData({ parentMsg: '' });
      }
    } catch (err) {
      wx.showToast({ title: '发送失败', icon: 'none' });
    }
  },

  async pushIdiom() {
    if (!this.data.dailyIdiom.idiom) return;
    try {
      await api.setDailyIdiom(this.data.dailyIdiom);
      wx.showToast({ title: '已推送至墨水屏', icon: 'success' });
    } catch (err) {
      wx.showToast({ title: '推送失败', icon: 'none' });
    }
  }
});
