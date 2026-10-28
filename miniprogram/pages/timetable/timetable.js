// pages/timetable/timetable.js
const api = require('../../utils/api.js');
const names = ['周一', '周二', '周三', '周四', '周五'];

Page({
  data: { timetableAll: {}, days: [], loaded: false, dirty: false },
  revision: 0,
  onShow() { this.loadTimetable(); },
  onPullDownRefresh() { this.loadTimetable().finally(() => wx.stopPullDownRefresh()); },
  async loadTimetable() {
    const revision = this.revision;
    try {
      const res = await api.getTimetable();
      if (!res.ok || this.data.dirty || revision !== this.revision) return;
      const all = res.timetable || {};
      const days = names.map((name, i) => {
        const day = all[String(i + 1)] || {};
        const courses = Array.isArray(day.courses) ? day.courses.slice(0, 6) : [];
        while (courses.length < 6) courses.push('');
        return { name, courses, note: day.note || '' };
      });
      this.setData({ timetableAll: all, days, loaded: true });
    } catch (_) { wx.showToast({ title: '课表读取失败', icon: 'none' }); }
  },
  onCourseInput(e) {
    const day = Number(e.currentTarget.dataset.day), period = Number(e.currentTarget.dataset.period);
    this.revision++;
    this.setData({ [`days[${day}].courses[${period}]`]: e.detail.value, dirty: true });
  },
  onNoteInput(e) {
    const day = Number(e.currentTarget.dataset.day);
    this.revision++;
    this.setData({ [`days[${day}].note`]: e.detail.value, dirty: true });
  },
  choosePhoto() {
    if (!this.data.loaded) return;
    const revision = this.revision;
    wx.chooseMedia({count:1, mediaType:['image'], sourceType:['camera','album'], success:r=>{
      const file=r.tempFiles&&r.tempFiles[0]; if(!file)return;
      if(file.size>8*1024*1024){wx.showToast({title:'照片不能超过 8 MB',icon:'none'});return}
      wx.getFileSystemManager().readFile({filePath:file.tempFilePath,encoding:'base64',success:async d=>{
        wx.showLoading({title:'识别课表'});
        try{const result=await api.ocrText(d.data,'课表');if(this.revision!==revision){wx.showToast({title:'课表已修改，请重新识别',icon:'none'});return}
          const days=this.data.days.map(day=>({...day,courses:[...day.courses]}));
          for(const row of result.fields.days||[]){const day=days[row.day-1];if(day)row.courses.forEach((course,i)=>{if(course.trim())day.courses[i]=course.trim()})}
          this.revision++;this.setData({days,dirty:true});wx.showToast({title:'请核对课表后保存',icon:'none'});
        }catch(_){wx.showToast({title:'识别失败',icon:'none'})}finally{wx.hideLoading()}
      },fail:()=>wx.showToast({title:'无法读取图片',icon:'none'})});
    }});
  },
  async saveWeekSchedule() {
    if (!this.data.loaded) return;
    const revision = this.revision;
    const all = { ...this.data.timetableAll };
    this.data.days.forEach((day, i) => {
      const existing = all[String(i + 1)] || {};
      const courses = Array.isArray(existing.courses) ? [...existing.courses] : [];
      day.courses.forEach((course, period) => { courses[period] = course.trim(); });
      all[String(i + 1)] = { ...existing, day_name: existing.day_name || day.name, courses, note: day.note.trim() };
    });
    try {
      const result = await api.setTimetable(all);
      if (!result || !result.ok) throw new Error('save failed');
      if (revision === this.revision) { this.revision++; this.setData({ timetableAll: result.timetable, dirty: false }); }
      wx.showToast({ title: '整周课表已保存', icon: 'success' });
    } catch (_) { wx.showToast({ title: '课表保存失败', icon: 'none' }); }
  }
});
