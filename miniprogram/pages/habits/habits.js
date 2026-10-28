// pages/habits/habits.js
const api = require('../../utils/api.js');

Page({
  openPhotoEntry() { wx.navigateTo({url:'/pages/content_entry/content_entry?index=1'}); },
  data: {
    habits: [],
    stats: { total: 0, done: 0, max_streak: 0, stars: 0, star_balance: 0 },
    starBank: { balance: 0, wishes: [], history: [] },
    selectedIcon: '📖',
    newHabitName: ''
  },

  onShow() {
    this.loadData();
  },

  onPullDownRefresh() {
    this.loadData().then(() => wx.stopPullDownRefresh());
  },

  async loadData() {
    try {
      const [resH, resS] = await Promise.all([api.getHabits(), api.getStars()]);
      if (resH && resH.ok) {
        this.setData({
          habits: resH.habits || [],
          stats: resH.stats || { total: 0, done: 0, max_streak: 0, stars: 0, star_balance: 0 }
        });
      }
      if (resS && resS.ok) {
        this.setData({ starBank: resS.bank || { balance: 0, wishes: [], history: [] } });
      }
    } catch (err) {
      console.error('loadData error:', err);
    }
  },

  async loadHabits() {
    return this.loadData();
  },

  async redeemWish(e) {
    const id = e.currentTarget.dataset.id;
    wx.showModal({
      title: '心愿兑换确认',
      content: '确定要帮孩子兑换该心愿吗？将扣除对应星星并向 Note 4 墨水屏发送表扬通知！',
      success: async (res) => {
        if (res.confirm) {
          try {
            const result = await api.redeemWish(id);
            if (result.ok) {
              wx.showToast({ title: '兑换成功！已推送墨水屏', icon: 'success' });
              this.loadData();
            } else {
              wx.showToast({ title: result.error || '兑换失败', icon: 'none' });
            }
          } catch (err) {
            wx.showToast({ title: '网络请求失败', icon: 'none' });
          }
        }
      }
    });
  },

  selectIcon(e) {
    this.setData({ selectedIcon: e.currentTarget.dataset.icon });
  },

  onNameInput(e) {
    this.setData({ newHabitName: e.detail.value });
  },

  async addHabit() {
    const name = this.data.newHabitName.trim();
    if (!name) {
      wx.showToast({ title: '请输入好习惯名称', icon: 'none' });
      return;
    }
    try {
      await api.addHabit(name, this.data.selectedIcon);
      this.setData({ newHabitName: '' });
      wx.showToast({ title: '好习惯已添加', icon: 'success' });
      this.loadHabits();
    } catch (err) {
      wx.showToast({ title: '添加失败', icon: 'none' });
    }
  },

  async toggleHabit(e) {
    const id = e.currentTarget.dataset.id;
    try {
      await api.toggleHabit(id);
      this.loadHabits();
    } catch (err) {
      console.error(err);
    }
  },

  async deleteHabit(e) {
    const id = e.currentTarget.dataset.id;
    try {
      await api.deleteHabit(id);
      this.loadHabits();
    } catch (err) {
      console.error(err);
    }
  }
});
