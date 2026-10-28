// pages/practice/practice.js
const api = require('../../utils/api.js');

Page({
  data: {
    tab: 'dictation',
    dictationTitle: '生字词听写',
    dictationInput: '',
    dictationSets: [],
    dictationRecords: [],
    poems: [],
    curPoemTitle: '',
    arithGrade: 2,
    arithCount: 10,
    vocabWord: '',
    vocabMean: '',
    vocabEx: '',
    vocabExCn: '',
    vocabList: [],
    readingArticles: [],
    curReadingId: '',
    curReadingTitle: '',
    newsRegion: '全国',
    newsRegionInput: '',
    newsWhitelistInput: '',
    newsBlacklistInput: '',
    newsSources: [],
    newsItems: []
  },

  onShow() {
    const app = getApp();
    const tab = app.globalData.practiceTab;
    if (tab) {
      this.setData({ tab });
      app.globalData.practiceTab = '';
    }
    this.loadAll();
  },

  onPullDownRefresh() {
    this.loadAll().then(() => wx.stopPullDownRefresh());
  },

  switchTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab });
  },
  openEntry(e) { wx.navigateTo({url:'/pages/content_entry/content_entry?index='+Number(e.currentTarget.dataset.index)}); },
  chooseReadingPhoto() { wx.navigateTo({url:'/pages/content_entry/content_entry?index=6'}); },

  async loadAll() {
    try {
      const ov = await api.getOverview();
      if (ov.ok) {
        this.setData({
          curPoemTitle: ov.poem ? ov.poem.title : '',
          curReadingId: ov.reading ? ov.reading.id : '',
          curReadingTitle: ov.reading ? ov.reading.title : '',
          vocabList: ov.vocab_words || [],
          arithGrade: ov.arith_config ? ov.arith_config.grade : 2,
          arithCount: ov.arith_config ? ov.arith_config.count : 10
        });
      }

      // Dictation
      const dRes = await api.getDictation();
      if (dRes.ok) {
        const rawStr = (dRes.words || []).map(w => w[0]).join(', ');
        const sets = (dRes.sets || []).map(s => ({
          ...s,
          preview: (s.words || []).map(w => w[0]).slice(0, 6).join('、') + ((s.words && s.words.length > 6) ? '...' : '')
        }));
        const records = (dRes.records || []).map(r => {
          const irregList = (r.irregular_words || []).map(it => (typeof it === 'string' ? it : (it.word || '')));
          return {
            ...r,
            time_str: new Date(r.completed_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            irregular_words_display: (r.irregular_words || []).map(it => {
              if (typeof it === 'string') return it;
              return `${it.word || ''}${it.reason ? `(${it.reason})` : ''}`;
            }).join('、'),
            words: (r.words || []).map(w => {
              const wordStr = w[0];
              const isWrong = (r.mistakes || []).includes(wordStr);
              const isIrreg = irregList.includes(wordStr);
              return {
                ...w,
                status: isWrong ? 'wrong' : isIrreg ? 'irregular' : 'correct',
                is_wrong: isWrong,
                is_irregular: isIrreg
              };
            })
          };
        });
        this.setData({
          dictationTitle: dRes.title || '生字词听写',
          dictationInput: rawStr,
          dictationSets: sets,
          dictationRecords: records
        });
      }

      // Poems
      const pRes = await api.getPoems();
      if (pRes.ok) this.setData({ poems: pRes.poems || [] });

      // Reading
      const rRes = await api.getReading();
      if (rRes.ok) this.setData({ readingArticles: rRes.articles || [] });

      // News
      const nRes = await api.getNews();
      if (nRes && nRes.ok) {
        this.setData({
          newsRegion: nRes.region || '全国',
          newsRegionInput: nRes.region === '全国' ? '' : (nRes.region || ''),
          newsWhitelistInput: (nRes.whitelist || []).join(', '),
          newsBlacklistInput: (nRes.blacklist || []).join(', '),
          newsSources: nRes.sources || [],
          newsItems: nRes.items || []
        });
      }
    } catch (err) {
      console.error(err);
    }
  },

  onDictationTitleInput(e) {
    this.setData({ dictationTitle: e.detail.value });
  },

  setPresetWords(e) {
    const presets = {
      '三年级精选': '温暖, 认真, 遵守, 奇迹, 灿烂, 严寒, 准备, 丰富',
      '二年级字词': '春雨, 阳光, 树木, 朋友, 快乐, 仔细, 帮助, 寻找',
      '期中易错词': '仔细, 演变, 聚集, 迅速, 既然, 赞美, 敬佩, 勇敢',
      '五年级上册必背': '吩咐, 茅亭, 榨油, 播种, 浇水, 爱慕, 体面, 开辟'
    };
    const t = e.currentTarget.dataset.type;
    this.setData({
      dictationTitle: t,
      dictationInput: presets[t] || ''
    });
  },

  onDictationInput(e) {
    this.setData({ dictationInput: e.detail.value });
  },

  chooseDictationPhoto() {
    const beforeTitle = this.data.dictationTitle, beforeInput = this.data.dictationInput;
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const tempPath = res.tempFiles[0].tempFilePath;
        wx.showLoading({ title: 'AI 识别生字中...' });
        wx.getFileSystemManager().readFile({
          filePath: tempPath,
          encoding: 'base64',
          success: async (fileRes) => {
            try {
              const title = this.data.dictationTitle || '拍照生字词';
              const ocrRes = await api.ocrWords(fileRes.data, title);
              wx.hideLoading();
              if (ocrRes.ok) {
                if (this.data.dictationTitle !== beforeTitle || this.data.dictationInput !== beforeInput) {
                  wx.showToast({ title: '词单已修改，请重新识别', icon: 'none' });
                  return;
                }
                this.setData({ dictationInput: ocrRes.raw_text });
                wx.showToast({ title: `识别出 ${ocrRes.count} 个词，请检查后保存`, icon: 'none' });
              } else {
                wx.showToast({ title: '识别失败，请重试', icon: 'none' });
              }
            } catch (e) {
              wx.hideLoading();
              wx.showToast({ title: '识别出错', icon: 'none' });
            }
          },
          fail: () => {
            wx.hideLoading();
            wx.showToast({ title: '读取图片失败', icon: 'none' });
          }
        });
      }
    });
  },

  async selectDictationSet(e) {
    const setId = e.currentTarget.dataset.setid;
    try {
      const res = await api.selectDictationSet(setId);
      if (res.ok) {
        wx.showToast({ title: `已设为当前：《${res.title}》`, icon: 'success' });
        this.loadAll();
      }
    } catch (e) {
      wx.showToast({ title: '切换失败', icon: 'none' });
    }
  },

  async deleteDictationSet(e) {
    const setId = e.currentTarget.dataset.setid;
    wx.showModal({
      title: '确认删除',
      content: '确定删除该听写词单吗？',
      success: async (sm) => {
        if (sm.confirm) {
          await api.deleteDictationSet(setId);
          wx.showToast({ title: '已删除', icon: 'success' });
          this.loadAll();
        }
      }
    });
  },

  cycleWordStatus(e) {
    const { recid, word } = e.currentTarget.dataset;
    const records = this.data.dictationRecords.map(r => {
      if (r.id === recid) {
        const words = r.words.map(w => {
          if (w[0] === word) {
            const cur = w.status || 'correct';
            const nextStatus = cur === 'correct' ? 'wrong' : cur === 'wrong' ? 'irregular' : 'correct';
            return {
              ...w,
              status: nextStatus,
              is_wrong: nextStatus === 'wrong',
              is_irregular: nextStatus === 'irregular'
            };
          }
          return w;
        });
        return { ...r, words };
      }
      return r;
    });
    this.setData({ dictationRecords: records });
  },

  toggleMistakeInRecord(e) {
    this.cycleWordStatus(e);
  },

  chooseAutoCheckPhoto(e) {
    const recid = e.currentTarget.dataset.recid || '';
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const tempPath = res.tempFiles[0].tempFilePath;
        wx.getFileSystemManager().readFile({
          filePath: tempPath,
          encoding: 'base64',
          success: async (fileRes) => {
            wx.showLoading({ title: 'AI 批改生字与规范中...' });
            try {
              const checkRes = await api.autoCheckDictationPhoto(recid, fileRes.data);
              wx.hideLoading();
              if (checkRes.ok) {
                const mistakes = checkRes.mistakes || [];
                const irreg = checkRes.irregular_words || [];
                const resObj = checkRes.check_result || {};
                const score = resObj.score == null ? 100 : resObj.score;
                let msg = `智能批改得分：${score}分！\n` +
                          `发现错字：${mistakes.length}个${mistakes.length ? ` (${mistakes.join('、')})` : ''}\n` +
                          `书写不规范：${irreg.length}个${irreg.length ? ` (${irreg.map(x => x.word || x).join('、')})` : ''}\n` +
                          `错字与不规范生字已全部录入错题消灭本！`;
                wx.showModal({
                  title: '🎉 听写拍照批改完成',
                  content: msg,
                  showCancel: false
                });
                this.loadAll();
              } else {
                wx.showToast({ title: '批改失败，请重试', icon: 'none' });
              }
            } catch (err) {
              wx.hideLoading();
              wx.showToast({ title: '批改异常: ' + (err.message || ''), icon: 'none' });
            }
          },
          fail: () => {
            wx.showToast({ title: '读取照片失败', icon: 'none' });
          }
        });
      }
    });
  },

  onRecordCommentInput(e) {
    const recid = e.currentTarget.dataset.recid;
    const val = e.detail.value;
    const records = this.data.dictationRecords.map(r => r.id === recid ? { ...r, comment_input: val } : r);
    this.setData({ dictationRecords: records });
  },

  async submitRecordReview(e) {
    const recid = e.currentTarget.dataset.recid;
    const target = this.data.dictationRecords.find(r => r.id === recid);
    if (!target) return;
    const mistakes = target.words.filter(w => w.status === 'wrong').map(w => w[0]);
    const irregularWords = target.words.filter(w => w.status === 'irregular').map(w => ({
      word: w[0],
      reason: '书写不规范，笔顺或间架结构需端正',
      suggestion: '注意规范书写与偏旁匀称'
    }));
    const comment = target.comment_input || '书写工整，继续加油！';
    try {
      const res = await api.reviewDictation(recid, mistakes, 2, comment, '', irregularWords);
      if (res.ok) {
        const total = mistakes.length + irregularWords.length;
        wx.showToast({
          title: `批改完成！${total > 0 ? `已将 ${total} 个字录入错题本` : '全对！'} +2⭐`,
          icon: 'success'
        });
        this.loadAll();
      }
    } catch (err) {
      wx.showToast({ title: '批改提交失败', icon: 'none' });
    }
  },

  async saveDictation() {
    const text = this.data.dictationInput.trim();
    const title = this.data.dictationTitle.trim() || '生字词听写';
    if (!text) {
      wx.showToast({ title: '请输入词语', icon: 'none' });
      return;
    }
    try {
      await api.setDictation(text, title);
      wx.showToast({ title: `《${title}》已推送到 Note 4`, icon: 'success' });
      this.loadAll();
    } catch (err) {
      wx.showToast({ title: '保存失败', icon: 'none' });
    }
  },

  async selectPoem(e) {
    const title = e.currentTarget.dataset.title;
    try {
      await api.setPoemTarget(title);
      this.setData({ curPoemTitle: title });
      wx.showToast({ title: '已设为背诵目标', icon: 'success' });
    } catch (err) {
      wx.showToast({ title: '设置失败', icon: 'none' });
    }
  },

  setGrade(e) {
    this.setData({ arithGrade: parseInt(e.currentTarget.dataset.g) });
  },

  setCount(e) {
    this.setData({ arithCount: parseInt(e.currentTarget.dataset.c) });
  },

  async saveArith() {
    try {
      await api.setArith(this.data.arithGrade, this.data.arithCount, 'all');
      wx.showToast({ title: '口算配置已保存', icon: 'success' });
    } catch (err) {
      wx.showToast({ title: '保存失败', icon: 'none' });
    }
  },

  onVWord(e) { this.setData({ vocabWord: e.detail.value }); },
  onVMean(e) { this.setData({ vocabMean: e.detail.value }); },
  onVEx(e) { this.setData({ vocabEx: e.detail.value }); },
  onVExCn(e) { this.setData({ vocabExCn: e.detail.value }); },

  async addVocab() {
    const { vocabWord, vocabMean, vocabEx, vocabExCn } = this.data;
    if (!vocabWord.trim()) {
      wx.showToast({ title: '请输入英文单词', icon: 'none' });
      return;
    }
    try {
      await api.addVocab(vocabWord.trim(), vocabMean.trim(), vocabEx.trim(), vocabExCn.trim());
      this.setData({ vocabWord: '', vocabMean: '', vocabEx: '', vocabExCn: '' });
      wx.showToast({ title: '已加入生词本', icon: 'success' });
      this.loadAll();
    } catch (err) {
      wx.showToast({ title: '添加失败', icon: 'none' });
    }
  },

  async deleteVocab(e) {
    const id = e.currentTarget.dataset.id;
    try {
      await api.deleteVocab(id);
      this.loadAll();
    } catch (err) {
      console.error(err);
    }
  },

  async selectReading(e) {
    const id = e.currentTarget.dataset.id;
    try {
      await api.setReadingTarget(id);
      this.setData({ curReadingId: id });
      wx.showToast({ title: '已同步至墨水屏', icon: 'success' });
    } catch (err) {
      wx.showToast({ title: '设置失败', icon: 'none' });
    }
  },

  onNewsRegionInput(e) {
    this.setData({ newsRegionInput: e.detail.value });
  },

  quickSelectRegion(e) {
    const reg = e.currentTarget.dataset.region;
    this.setData({ newsRegionInput: reg === '全国' ? '' : reg });
    this.saveNewsRegion();
  },

  async saveNewsRegion() {
    const reg = this.data.newsRegionInput.trim() || '全国';
    wx.showLoading({ title: '保存中...' });
    try {
      await api.updateNewsConfig({ region: reg });
      wx.showToast({ title: `地区设为 ${reg}`, icon: 'success' });
      this.loadAll();
    } catch (e) {
      wx.showToast({ title: '保存失败', icon: 'none' });
    }
  },

  onNewsWhitelistInput(e) {
    this.setData({ newsWhitelistInput: e.detail.value });
  },

  onNewsBlacklistInput(e) {
    this.setData({ newsBlacklistInput: e.detail.value });
  },

  async saveNewsKeywords() {
    const w = this.data.newsWhitelistInput.split(/[,，、]/).map(s => s.trim()).filter(Boolean);
    const b = this.data.newsBlacklistInput.split(/[,，、]/).map(s => s.trim()).filter(Boolean);
    wx.showLoading({ title: '保存中...' });
    try {
      await api.updateNewsConfig({ whitelist: w, blacklist: b });
      wx.showToast({ title: '偏好已保存', icon: 'success' });
      this.loadAll();
    } catch (e) {
      wx.showToast({ title: '保存失败', icon: 'none' });
    }
  },

  async deleteNews(e) {
    const id = e.currentTarget.dataset.id;
    try {
      await api.deleteNewsItem(id);
      wx.showToast({ title: '已隐藏该报道', icon: 'success' });
      this.loadAll();
    } catch (e) {
      wx.showToast({ title: '操作失败', icon: 'none' });
    }
  }
});
