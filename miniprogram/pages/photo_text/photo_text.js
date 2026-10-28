const api = require('../../utils/api.js');
Page({
  data: {text: '', busy: false},
  onText(e) { this.setData({ text: e.detail.value }); },
  choosePhoto() {
    if (this.data.busy) return;
    wx.chooseMedia({count: 1, mediaType: ['image'], sourceType: ['album', 'camera'], success: result => {
      const file = result.tempFiles && result.tempFiles[0];
      if (!file) return;
      if (file.size > 8 * 1024 * 1024) { wx.showToast({title:'照片不能超过 8 MB', icon:'none'}); return; }
      wx.getFileSystemManager().readFile({filePath:file.tempFilePath, encoding:'base64', success: async data => {
        this.setData({busy:true}); wx.showLoading({title:'识别中'});
        try { const response = await api.ocrText(data.data); this.setData({text:response.text}); wx.showToast({title:'识别完成，请核对',icon:'none'}); }
        catch (_) { wx.showToast({title:'识别失败，请重试',icon:'none'}); }
        finally { this.setData({busy:false}); wx.hideLoading(); }
      }, fail: () => wx.showToast({title:'无法读取图片',icon:'none'})});
    }});
  },
  copyText() {
    if (!this.data.text.trim()) {wx.showToast({title:'请先输入或识别文字',icon:'none'});return;}
    wx.setClipboardData({data:this.data.text});
  }
});
