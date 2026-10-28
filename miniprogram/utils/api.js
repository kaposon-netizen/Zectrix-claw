// utils/api.js
const app = getApp();

function getBaseUrl() {
  const customBase = wx.getStorageSync('custom_base_url');
  return customBase || (app && app.globalData && app.globalData.baseUrl ? app.globalData.baseUrl : 'http://127.0.0.1:8000');
}

function request(url, method = 'GET', data = {}, extraHeaders = {}) {
  const base = getBaseUrl();
  const fullUrl = base + url;
  const familyToken = wx.getStorageSync('family_session_token');
  const hasCredential = !!familyToken || !!extraHeaders['X-Admin-Token'] || url === '/api/session/login';
  if (hasCredential && !/^https:\/\//i.test(base)) {
    wx.showToast({ title: '登录和令牌操作需要 HTTPS', icon: 'none' });
    return Promise.reject(new Error('HTTPS required'));
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url: fullUrl,
      method: method,
      data: data,
      header: {
        'Content-Type': 'application/json',
        ...(familyToken ? { Authorization: 'Bearer ' + familyToken } : {}),
        ...extraHeaders
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          wx.showToast({ title: '网络请求异常: ' + res.statusCode, icon: 'none' });
          reject(res);
        }
      },
      fail(err) {
        console.error('Request failed:', err);
        wx.showToast({ title: '无法连接主机 (' + base + ')', icon: 'none' });
        reject(err);
      }
    });
  });
}

module.exports = {
  getBaseUrl,
  familyLogin: (name, password) => request('/api/session/login', 'POST', { name, password }),
  familyLogout: () => request('/api/session/logout', 'POST'),
  getGradePresets: (category, grades) => request('/api/pocketclaw/mp/presets?category=' + encodeURIComponent(category) + '&grades=' + encodeURIComponent(grades)),
  importGradePresets: (category, ids) => request('/api/pocketclaw/mp/presets/import', 'POST', {category, ids}),
  ocrText: (image, target = '') => request('/api/pocketclaw/mp/ocr_text', 'POST', { image, target }),
  familyMe: () => request('/api/session/me'),
  getChildProfile: (childId = '') => request('/api/pocketclaw/mp/child_profile' + (childId ? '?child_id=' + encodeURIComponent(childId) : '')),
  saveChildProfile: (profile, childId = '') => request('/api/pocketclaw/mp/child_profile' + (childId ? '?child_id=' + encodeURIComponent(childId) : ''), 'PUT', profile),
  issueDevice: (childId, deviceId) => request('/api/session/devices', 'POST', { child_id: childId, device_id: deviceId }),
  revokeDevice: (childId, deviceId) => request('/api/session/devices/revoke', 'POST', { child_id: childId, device_id: deviceId }),
  captureScreen: (deviceId = '') => request('/api/pocketclaw/mp/screen_snapshot', 'POST',
    { device_id: deviceId }, { 'X-Admin-Token': wx.getStorageSync('screen_admin_token') || '' }),
  getArtPreviewUrl: (artId) => `${getBaseUrl()}/api/pocketclaw/mp/art_preview/${artId}`,

  // 综合态势与设备模式切屏
  getOverview: (childId) => request('/api/pocketclaw/mp/overview' + (childId ? '?child_id=' + childId : '')),
  switchDeviceMode: (mode, extra = {}) => request('/api/pocketclaw/mp/mode', 'POST', { mode, ...extra }),

  // 番茄钟远程专注控制
  getFocusStatus: (childId) => request('/api/pocketclaw/mp/focus' + (childId ? '?child_id=' + childId : '')),
  controlFocus: (action, minutes = 25, style = 'spark', kind = 'pomodoro', childId = '') =>
    request('/api/pocketclaw/mp/focus', 'POST', { action, minutes, style, kind, child_id: childId }),

  // 作业清单
  getTasks: (childId) => request('/api/pocketclaw/mp/tasks' + (childId ? '?child_id=' + childId : '')),
  addTask: (text, subject = '', childId = '') => request('/api/pocketclaw/mp/tasks', 'POST', { text, subject, child_id: childId }),
  toggleTask: (taskId, childId = '') => request(`/api/pocketclaw/mp/tasks/${taskId}/toggle` + (childId ? '?child_id=' + childId : ''), 'POST'),
  deleteTask: (taskId, childId = '') => request(`/api/pocketclaw/mp/tasks/${taskId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),
  clearDoneTasks: (childId = '') => request('/api/pocketclaw/mp/tasks/clear_done' + (childId ? '?child_id=' + childId : ''), 'DELETE'),
  quickInputTasks: (rawText, clearExisting = false) => request('/api/pocketclaw/parent/quick_input', 'POST', { raw_tasks: rawText, clear_existing: clearExisting }),

  // 习惯打卡
  getHabits: (childId) => request('/api/pocketclaw/mp/habits' + (childId ? '?child_id=' + childId : '')),
  addHabit: (name, icon = '⭐', childId = '') => request('/api/pocketclaw/mp/habits', 'POST', { name, icon, child_id: childId }),
  toggleHabit: (habitId, childId = '') => request(`/api/pocketclaw/mp/habits/${habitId}/toggle` + (childId ? '?child_id=' + childId : ''), 'POST'),
  deleteHabit: (habitId, childId = '') => request(`/api/pocketclaw/mp/habits/${habitId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),

  // 星星存折与心愿兑换
  getStars: (childId) => request('/api/pocketclaw/mp/stars' + (childId ? '?child_id=' + childId : '')),
  addStars: (count, reason = '', childId = '') => request('/api/pocketclaw/mp/stars/add', 'POST', { count, reason, child_id: childId }),
  addWish: (title, stars_needed = 20, icon = '🎁', childId = '') => request('/api/pocketclaw/mp/stars/wishes', 'POST', { title, stars_needed, icon, child_id: childId }),
  redeemWish: (wishId, childId = '') => request(`/api/pocketclaw/mp/stars/wishes/${wishId}/redeem` + (childId ? '?child_id=' + childId : ''), 'POST'),
  deleteWish: (wishId, childId = '') => request(`/api/pocketclaw/mp/stars/wishes/${wishId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),

  // 课程表
  getTimetable: (childId) => request('/api/pocketclaw/mp/timetable' + (childId ? '?child_id=' + childId : '')),
  setTimetable: (timetable, childId = '') => request('/api/pocketclaw/mp/timetable', 'POST', { timetable, child_id: childId }),
  getTodaySchedule: (childId) => request('/api/pocketclaw/mp/timetable/today' + (childId ? '?child_id=' + childId : '')),

  // 错题消灭本
  getMistakes: (childId, category = '') => request('/api/pocketclaw/mp/mistakes' + (childId ? '?child_id=' + childId : '') + (category ? '&category=' + category : '')),
  addMistake: (data) => request('/api/pocketclaw/mp/mistakes', 'POST', data),
  deleteMistake: (mistakeId, childId = '') => request(`/api/pocketclaw/mp/mistakes/${mistakeId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),
  clearMistakes: (childId = '') => request('/api/pocketclaw/mp/mistakes' + (childId ? '?child_id=' + childId : ''), 'DELETE'),

  // 每日成语
  getIdiom: (childId) => request('/api/pocketclaw/mp/idiom' + (childId ? '?child_id=' + childId : '')),
  setDailyIdiom: (idiomData, childId = '') => request('/api/pocketclaw/mp/idiom/target', 'POST', { ...idiomData, child_id: childId, push: true }),

  // 听写与古诗
  getDictation: (childId) => request('/api/pocketclaw/mp/dictation' + (childId ? '?child_id=' + childId : '')),
  setDictation: (rawText, title = '生字词听写', childId = '') => request('/api/pocketclaw/mp/dictation', 'POST', { raw_text: rawText, title: title, child_id: childId, push: true, save_as_set: true }),
  selectDictationSet: (setId, childId = '') => request('/api/pocketclaw/mp/dictation/select_set', 'POST', { set_id: setId, child_id: childId, push: true }),
  deleteDictationSet: (setId, childId = '') => request('/api/pocketclaw/mp/dictation/delete_set', 'POST', { set_id: setId, child_id: childId }),
  reviewDictation: (recordId, mistakes, stars = 1, comment = '', childId = '', irregularWords = []) => request('/api/pocketclaw/mp/dictation/review', 'POST', { record_id: recordId, mistakes: mistakes, irregular_words: irregularWords, stars: stars, comment: comment, child_id: childId }),
  autoCheckDictationPhoto: (recordId, imageBase64, childId = '') => request('/api/pocketclaw/mp/dictation/auto_check', 'POST', { record_id: recordId, image: imageBase64, child_id: childId }),
  ocrWords: (imageBase64, title = '拍照生字词', childId = '') => request('/api/pocketclaw/mp/ocr_words', 'POST', { image: imageBase64, title: title, child_id: childId, push: false, save_as_set: false }),
  getPoems: (childId) => request('/api/pocketclaw/mp/poems' + (childId ? '?child_id=' + childId : '')),
  setPoemTarget: (title, childId = '') => request('/api/pocketclaw/mp/poems/target', 'POST', { title, child_id: childId, push: true }),

  // 口算
  getArith: (childId) => request('/api/pocketclaw/mp/arith' + (childId ? '?child_id=' + childId : '')),
  setArith: (grade, count, opType = 'all', childId = '') => request('/api/pocketclaw/mp/arith', 'POST', { grade, count, op_type: opType, child_id: childId, push: true }),

  // 英语生词与阅读
  getVocab: (childId) => request('/api/pocketclaw/mp/vocab' + (childId ? '?child_id=' + childId : '')),
  addVocab: (word, meaning, example = '', exampleCn = '', childId = '') => request('/api/pocketclaw/mp/vocab', 'POST', { word, meaning, example, example_cn: exampleCn, child_id: childId }),
  deleteVocab: (wordId, childId = '') => request(`/api/pocketclaw/mp/vocab/${wordId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),
  getReading: (childId) => request('/api/pocketclaw/mp/reading' + (childId ? '?child_id=' + childId : '')),
  setReadingTarget: (articleId, childId = '') => request('/api/pocketclaw/mp/reading/target', 'POST', { article_id: articleId, child_id: childId, push: true }),
  ocrReading: (imageBase64, level = '小学英语', childId = '') => request('/api/pocketclaw/mp/ocr_reading', 'POST', { image: imageBase64, level: level, child_id: childId, push: true }),

  // 伴学屏保与16灰阶世界名画
  getScreensaver: (childId) => request('/api/pocketclaw/mp/screensaver' + (childId ? '?child_id=' + childId : '')),
  setScreensaverConfig: (cfg) => request('/api/pocketclaw/mp/screensaver/config', 'POST', cfg),
  pushScreensaver: (payload) => request('/api/pocketclaw/mp/screensaver/push', 'POST', payload),
  addCountdown: (title, target_date, slogan = '', childId = '') =>
    request('/api/pocketclaw/mp/screensaver/countdown', 'POST', { title, target_date, slogan, child_id: childId }),
  deleteCountdown: (eventId, childId = '') =>
    request(`/api/pocketclaw/mp/screensaver/countdown/${eventId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),

  // 设备功能导航菜单排序
  getMenu: (childId) => request('/api/pocketclaw/mp/menu' + (childId ? '?child_id=' + childId : '')),
  setMenuOrder: (order, childId = '') => request('/api/pocketclaw/mp/menu/order', 'POST', { order, child_id: childId }),
  resetMenuOrder: (resetClicks = false, childId = '') => request('/api/pocketclaw/mp/menu/reset', 'POST', { reset_clicks: resetClicks, child_id: childId }),

  // 便签推屏
  sendMessage: (text, childId = '') => request('/api/pocketclaw/mp/message', 'POST', { text, child_id: childId }),

  // 学习报告
  getReport: (childId) => request('/api/pocketclaw/mp/report' + (childId ? '?child_id=' + childId : '')),

  // 系统设置：学伴人设与 LLM/OCR 运行模式
  getSettings: (childId = '') => request('/api/pocketclaw/mp/settings' + (childId ? '?child_id=' + childId : '')),
  saveSettings: (payload, childId = '') => request('/api/pocketclaw/mp/settings' + (childId ? '?child_id=' + childId : ''), 'POST', payload),
  testLLM: (payload) => request('/api/pocketclaw/mp/settings/test_llm', 'POST', payload),
  resetSettings: (childId = '') => request('/api/pocketclaw/mp/settings/reset' + (childId ? '?child_id=' + childId : ''), 'POST'),
  getCompanion: (childId = '') => request('/api/pocketclaw/mp/companion' + (childId ? '?child_id=' + childId : '')),
  saveCompanion: (payload, childId = '') => request('/api/pocketclaw/mp/companion' + (childId ? '?child_id=' + childId : ''), 'POST', payload),

  // 19大功能权限与开关控制
  getFeatures: (childId = '') => request('/api/pocketclaw/mp/features' + (childId ? '?child_id=' + childId : '')),
  toggleFeature: (feature, enabled, childId = '') => request('/api/pocketclaw/mp/features/toggle', 'POST', { feature, enabled, child_id: childId }),

  // 全功能内容修改与自定义库 CRUD
  updateTask: (taskId, data) => request(`/api/pocketclaw/mp/tasks/${taskId}`, 'PUT', data),
  updateVocab: (wordId, data) => request(`/api/pocketclaw/mp/vocab/${wordId}`, 'PUT', data),
  updateHabit: (habitId, data) => request(`/api/pocketclaw/mp/habits/${habitId}`, 'PUT', data),
  updateWish: (wishId, data) => request(`/api/pocketclaw/mp/stars/wishes/${wishId}`, 'PUT', data),
  updateMistake: (mistakeId, data) => request(`/api/pocketclaw/mp/mistakes/${mistakeId}`, 'PUT', data),
  updateCountdown: (eventId, data) => request(`/api/pocketclaw/mp/screensaver/countdown/${eventId}`, 'PUT', data),
  updateDictationSet: (setId, data) => request(`/api/pocketclaw/mp/dictation/${setId}`, 'PUT', data),

  // 古诗自定义库 CRUD
  getCustomPoems: (childId = '') => request('/api/pocketclaw/mp/poems/custom' + (childId ? '?child_id=' + childId : '')),
  addCustomPoem: (data) => request('/api/pocketclaw/mp/poems/custom', 'POST', data),
  updateCustomPoem: (poemId, data) => request(`/api/pocketclaw/mp/poems/custom/${poemId}`, 'PUT', data),
  deleteCustomPoem: (poemId, childId = '') => request(`/api/pocketclaw/mp/poems/custom/${poemId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),

  // 成语自定义库 CRUD
  getCustomIdioms: (childId = '') => request('/api/pocketclaw/mp/idiom/custom' + (childId ? '?child_id=' + childId : '')),
  addCustomIdiom: (data) => request('/api/pocketclaw/mp/idiom/custom', 'POST', data),
  updateCustomIdiom: (idiomId, data) => request(`/api/pocketclaw/mp/idiom/custom/${idiomId}`, 'PUT', data),
  deleteCustomIdiom: (idiomId, childId = '') => request(`/api/pocketclaw/mp/idiom/custom/${idiomId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),

  // 口算靶向题库 CRUD
  getCustomArith: (childId = '') => request('/api/pocketclaw/mp/arith/custom' + (childId ? '?child_id=' + childId : '')),
  addCustomArith: (data) => request('/api/pocketclaw/mp/arith/custom', 'POST', data),
  updateCustomArith: (itemId, data) => request(`/api/pocketclaw/mp/arith/custom/${itemId}`, 'PUT', data),
  deleteCustomArith: (itemId, childId = '') => request(`/api/pocketclaw/mp/arith/custom/${itemId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),

  // 分级阅读短文 CRUD
  addCustomReading: (data) => request('/api/pocketclaw/mp/reading/custom', 'POST', data),
  updateCustomReading: (articleId, data) => request(`/api/pocketclaw/mp/reading/custom/${articleId}`, 'PUT', data),
  deleteCustomReading: (articleId) => request(`/api/pocketclaw/mp/reading/custom/${articleId}`, 'DELETE'),

  // 猜猜看谜题 CRUD
  getCustomGuess: (childId = '') => request('/api/pocketclaw/mp/guess/custom' + (childId ? '?child_id=' + childId : '')),
  addCustomGuess: (data) => request('/api/pocketclaw/mp/guess/custom', 'POST', data),
  updateCustomGuess: (itemId, data) => request(`/api/pocketclaw/mp/guess/custom/${itemId}`, 'PUT', data),
  deleteCustomGuess: (itemId, childId = '') => request(`/api/pocketclaw/mp/guess/custom/${itemId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),

  // 科学常识核实 CRUD
  getCustomFacts: (childId = '') => request('/api/pocketclaw/mp/facts/custom' + (childId ? '?child_id=' + childId : '')),
  addCustomFact: (data) => request('/api/pocketclaw/mp/facts/custom', 'POST', data),
  updateCustomFact: (itemId, data) => request(`/api/pocketclaw/mp/facts/custom/${itemId}`, 'PUT', data),
  deleteCustomFact: (itemId, childId = '') => request(`/api/pocketclaw/mp/facts/custom/${itemId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),

  // 费曼讲解主题 CRUD
  getFeynmanTopics: (childId = '') => request('/api/pocketclaw/mp/feynman/custom' + (childId ? '?child_id=' + childId : '')),
  addFeynmanTopic: (data) => request('/api/pocketclaw/mp/feynman/custom', 'POST', data),
  updateFeynmanTopic: (topicId, data) => request(`/api/pocketclaw/mp/feynman/custom/${topicId}`, 'PUT', data),
  deleteFeynmanTopic: (topicId, childId = '') => request(`/api/pocketclaw/mp/feynman/custom/${topicId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),

  // 诗词飞花令 CRUD
  getFeihua: (childId = '') => request('/api/pocketclaw/mp/feihua/custom' + (childId ? '?child_id=' + childId : '')),
  addFeihua: (data) => request('/api/pocketclaw/mp/feihua/custom', 'POST', data),
  deleteFeihua: (data) => request('/api/pocketclaw/mp/feihua/custom', 'DELETE', data),

  // 小学新鲜事 (赛事/展会/活动/新闻)
  getNews: (childId = '') => request('/api/pocketclaw/mp/news' + (childId ? '?child_id=' + childId : '')),
  updateNewsConfig: (payload, childId = '') => request('/api/pocketclaw/mp/news/config', 'POST', { ...payload, child_id: childId }),
  deleteNewsItem: (itemId, childId = '') => request(`/api/pocketclaw/mp/news/${itemId}` + (childId ? '?child_id=' + childId : ''), 'DELETE'),
  restoreNewsItem: (itemId, childId = '') => request('/api/pocketclaw/mp/news/restore', 'POST', { item_id: itemId, child_id: childId }),
  addNewsItem: (payload, childId = '') => request('/api/pocketclaw/mp/news', 'POST', { ...payload, child_id: childId }),
  updateNewsItem: (itemId, payload, childId = '') => request(`/api/pocketclaw/mp/news/${itemId}`, 'PUT', { ...payload, child_id: childId }),
  addNewsSource: (payload, childId = '') => request('/api/pocketclaw/mp/news/source', 'POST', { ...payload, child_id: childId }),
  deleteNewsSource: (sourceId, childId = '') => request('/api/pocketclaw/mp/news/source/delete', 'POST', { source_id: sourceId, child_id: childId })
};
