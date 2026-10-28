// pages/tasks/tasks.js
const api = require('../../utils/api.js');

Page({
  openPhotoEntry() { wx.navigateTo({url:'/pages/content_entry/content_entry?index=0'}); },
  data: {
    rawInput: '',
    singleInput: '',
    tasks: [],
    stats: { total: 0, done: 0 }
  },

  onShow() {
    this.loadTasks();
  },

  onPullDownRefresh() {
    this.loadTasks().then(() => wx.stopPullDownRefresh());
  },

  async loadTasks() {
    try {
      const res = await api.getTasks();
      if (res.ok) {
        this.setData({
          tasks: res.tasks || [],
          stats: res.stats || { total: 0, done: 0 }
        });
      }
    } catch (err) {
      console.error('loadTasks error:', err);
    }
  },

  onRawInput(e) {
    this.setData({ rawInput: e.detail.value });
  },

  onSingleInput(e) {
    this.setData({ singleInput: e.detail.value });
  },

  async submitRaw(e) {
    const clear = e.currentTarget.dataset.clear;
    const text = this.data.rawInput.trim();
    if (!text) {
      wx.showToast({ title: '请先粘贴作业通知', icon: 'none' });
      return;
    }
    try {
      const res = await api.quickInputTasks(text, clear);
      if (res.ok) {
        wx.showToast({ title: '已同步推送到墨水屏', icon: 'success' });
        this.setData({ rawInput: '' });
        this.loadTasks();
      }
    } catch (err) {
      wx.showToast({ title: '解析失败', icon: 'none' });
    }
  },

  async addSingle() {
    const text = this.data.singleInput.trim();
    if (!text) return;
    try {
      await api.addTask(text);
      this.setData({ singleInput: '' });
      wx.showToast({ title: '已添加作业', icon: 'success' });
      this.loadTasks();
    } catch (err) {
      wx.showToast({ title: '添加失败', icon: 'none' });
    }
  },

  async toggleTask(e) {
    const id = e.currentTarget.dataset.id;
    try {
      await api.toggleTask(id);
      this.loadTasks();
    } catch (err) {
      console.error(err);
    }
  },

  async deleteTask(e) {
    const id = e.currentTarget.dataset.id;
    try {
      await api.deleteTask(id);
      this.loadTasks();
    } catch (err) {
      console.error(err);
    }
  },

  onEditTask(e) {
    const task = e.currentTarget.dataset.task;
    if (!task) return;
    wx.showModal({
      title: '修改作业',
      editable: true,
      placeholderText: task.text,
      content: task.text,
      success: async (res) => {
        if (res.confirm && res.content && res.content.trim()) {
          try {
            await api.updateTask(task.id, { text: res.content.trim(), subject: task.subject });
            wx.showToast({ title: '已修改并同步', icon: 'success' });
            this.loadTasks();
          } catch (err) {
            wx.showToast({ title: '修改失败', icon: 'none' });
          }
        }
      }
    });
  },

  async clearDone() {
    try {
      await api.clearDoneTasks();
      wx.showToast({ title: '已清空已打勾任务', icon: 'none' });
      this.loadTasks();
    } catch (err) {
      console.error(err);
    }
  }
});
