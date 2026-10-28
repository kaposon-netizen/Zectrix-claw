// pages/mistakes/mistakes.js
const api = require('../../utils/api.js');

Page({
  openPhotoEntry() { wx.navigateTo({url:'/pages/content_entry/content_entry?index=8'}); },
  data: {
    mistakes: []
  },

  onShow() {
    this.loadMistakes();
  },

  onPullDownRefresh() {
    this.loadMistakes().then(() => wx.stopPullDownRefresh());
  },

  async loadMistakes() {
    try {
      const res = await api.getMistakes();
      if (res.ok) {
        this.setData({ mistakes: res.mistakes || [] });
      }
    } catch (err) {
      console.error(err);
    }
  },

  async eliminate(e) {
    const id = e.currentTarget.dataset.id;
    try {
      await api.deleteMistake(id);
      wx.showToast({ title: '错题已消灭！', icon: 'success' });
      this.loadMistakes();
    } catch (err) {
      wx.showToast({ title: '操作失败', icon: 'none' });
    }
  },

  async clearAll() {
    try {
      await api.clearMistakes();
      wx.showToast({ title: '错题本已清空', icon: 'none' });
      this.loadMistakes();
    } catch (err) {
      console.error(err);
    }
  }
});
