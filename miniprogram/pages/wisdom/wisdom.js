const api = require('../../utils/api.js');

Page({
  data: { artList: [], baseUrl: '' },
  onShow() { this.loadData(); },
  async loadData() {
    try {
      const result = await api.getOverview();
      if (result.ok) this.setData({ artList: result.art_list || [], baseUrl: api.getBaseUrl() });
    } catch (error) { console.error('加载素养视野失败', error); }
  },
  onPullDownRefresh() { this.loadData().finally(() => wx.stopPullDownRefresh()); },
  openPhotoEntry(e) { wx.navigateTo({url:'/pages/content_entry/content_entry?index='+Number(e.currentTarget.dataset.index)}); },
  openNews() {
    getApp().globalData.practiceTab = 'news';
    wx.switchTab({ url: '/pages/practice/practice' });
  },
  async pushArt(e) {
    try {
      const result = await api.pushScreensaver({ mode: 'art', art_id: e.currentTarget.dataset.id, speak: false });
      wx.showToast({ title: result.pushed ? '已推送到设备' : '设备未在线', icon: 'none' });
    } catch (error) { console.error('推送画作失败', error); }
  }
});
