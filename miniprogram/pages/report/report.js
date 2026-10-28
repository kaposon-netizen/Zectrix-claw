// pages/report/report.js
const api = require('../../utils/api.js');
const app = getApp();

const COMP_PRESETS = {
  classic: {
    name: "虾崽",
    style: "温柔耐心",
    persona: "你的第一只伴学小龙虾，天生好奇、话里带笑。最爱把话头交回给小朋友，遇到有趣的知识会兴奋地举例子，讲完只留一个小问题等他接。遇到自己不擅长的领域，会大方地把专家朋友请出来。",
    guidance: "你在一块黑白墨水屏便利贴上和小朋友说话，屏幕很小、只能靠语音听。只输出要对小朋友讲的话本身，口语、亲切。每次回复最多两句、总共不超过 40 个字——说完就停，把话头留给孩子；英语练习时英文只说一句短句，再给一句中文意思。绝不要加脚注编号，绝不要出现「说明：」，也不要复述你的教学设计。结尾留一个短问题（不超过 12 个字）。"
  },
  humor: {
    name: "大白",
    style: "幽默风趣",
    persona: "风趣幽默的机器小学伴，幽默乐观，擅长用生活中的冷笑话或有趣比喻解释复杂问题，喜欢用轻松的方式鼓励孩子克服学习困难。",
    guidance: "在黑白墨水屏便利贴上回答，口吻幽默风趣，两句话以内不超过 40 字，结尾留一个趣味反问启发思考。"
  },
  socratic: {
    name: "小聪",
    style: "严谨学霸",
    persona: "苏格拉底式的思维导师，绝不直接给出题目的答案，善于通过分步拆解、循序渐进的启发式提问引导孩子自己推导出正确答案。",
    guidance: "绝不直接给最终答案！给出第一步思考的线索，每次回答两句以内（不超过40字），并反问孩子下一步该怎么算。"
  }
};

const PROVIDER_LIST = [
  { key: "deepseek", name: "DeepSeek (深度求索 · 推荐)", base: "https://api.deepseek.com/v1", model: "deepseek-chat", vision: "gpt-4o-mini" },
  { key: "qwen", name: "通义千问 (阿里云)", base: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus", vision: "qwen-vl-plus" },
  { key: "doubao", name: "火山方舟/豆包 (字节跳动)", base: "https://ark.cn-beijing.volces.com/api/v3", model: "doubao-seed-2-1-turbo-260628", vision: "doubao-seed-1-5-vision" },
  { key: "zhipu", name: "智谱清言 (GLM-4)", base: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash", vision: "glm-4v-flash" },
  { key: "openai", name: "OpenAI (GPT-4o)", base: "https://api.openai.com/v1", model: "gpt-4o-mini", vision: "gpt-4o-mini" },
  { key: "ollama", name: "本地 Ollama / vLLM", base: "http://localhost:11434/v1", model: "qwen2.5:7b", vision: "llava:7b" },
  { key: "custom", name: "自定义接口", base: "", model: "", vision: "" }
];

Page({
  openHabits() { wx.navigateTo({ url: '/pages/habits/habits' }); },
  openTimetable() { wx.navigateTo({ url: '/pages/timetable/timetable' }); },
  openTasks() { wx.navigateTo({ url: '/pages/tasks/tasks' }); },
  data: {
    taskRate: 0,
    focusMin: 0,
    vocabCount: 0,
    badges: [],
    baseUrl: 'http://127.0.0.1:8000',
    familyName: '', familyPassword: '', familyLoggedIn: false, familyChildren: [],
    profileChildIndex: 0, profileName: '', profileGrade: 0, profileGender: 0, profileInterests: '',
    profileGrades: ['未设置', '一年级', '二年级', '三年级', '四年级', '五年级', '六年级'],
    profileGenders: ['未设置', '男', '女', '其他'],
    deviceIdInput: '', issuedDeviceToken: '',
    screenTokenInput: '',
    hasScreenToken: false,

    // Companion
    compName: '虾崽',
    compStyle: '温柔耐心',
    compPersona: '',
    compGuidance: '',
    styleOptions: ['温柔耐心', '幽默风趣', '严谨学霸', '元气满满', '双语外教', '自定义'],
    styleIndex: 0,

    // LLM
    llmMode: 'online',
    providerIndex: 0,
    providerNames: PROVIDER_LIST.map(p => p.name),
    llmModel: 'deepseek-chat',
    llmBaseUrl: 'https://api.deepseek.com/v1',
    llmApiKey: '',
    showKey: false,
    testResult: '',

    // OCR
    ocrMode: 'online',
    ocrModel: 'gpt-4o-mini',

    // Features Toggle
    featuresList: [],
    enabledCount: 19
  },

  onShow() {
    if (app && app.globalData) {
      this.setData({ baseUrl: api.getBaseUrl(), hasScreenToken: !!wx.getStorageSync('screen_admin_token'), screenTokenInput: '', familyLoggedIn: !!wx.getStorageSync('family_session_token') });
      if (wx.getStorageSync('family_session_token')) this.loadFamily();
      else this.loadChildProfile();
    }
    this.loadReport();
    this.loadSettings();
    this.loadFeatures();
  },

  onPullDownRefresh() {
    Promise.all([this.loadReport(), this.loadSettings(), this.loadFeatures()]).then(() => wx.stopPullDownRefresh());
  },

  async loadFeatures() {
    try {
      const res = await api.getFeatures();
      if (res && res.ok && res.features) {
        const enabledCount = res.features.filter(f => f.enabled).length;
        this.setData({
          featuresList: res.features,
          enabledCount: enabledCount
        });
      }
    } catch (err) {
      console.error('loadFeatures error:', err);
    }
  },

  async onToggleFeature(e) {
    const key = e.currentTarget.dataset.key;
    const enabled = e.detail.value;
    try {
      const res = await api.toggleFeature(key, enabled);
      if (res && res.ok) {
        wx.showToast({ title: `${res.feature} 已${enabled ? '启用' : '停用'}`, icon: 'none' });
        this.loadFeatures();
      }
    } catch (err) {
      wx.showToast({ title: '切换失败', icon: 'none' });
    }
  },

  async loadReport() {
    try {
      const res = await api.getReport();
      if (res && res.ok) {
        this.setData({
          taskRate: res.task_completion_rate || 0,
          focusMin: res.focus_minutes || 0,
          vocabCount: res.vocab_count || 0,
          badges: res.badges || []
        });
      }
    } catch (err) {
      console.error(err);
    }
  },

  async loadSettings() {
    try {
      const familyMode = !!wx.getStorageSync('family_session_token');
      const res = familyMode ? await api.getCompanion() : await api.getSettings();
      if (res && res.ok) {
        const comp = familyMode ? (res.config || {}) : (res.companion || {});
        const llm = res.llm || {};
        const ocr = res.ocr || {};

        let sIdx = this.data.styleOptions.indexOf(comp.style || '温柔耐心');
        if (sIdx < 0) sIdx = 0;

        let pIdx = PROVIDER_LIST.findIndex(p => p.key === llm.provider);
        if (pIdx < 0) pIdx = 0;

        this.setData({
          compName: comp.name || '虾崽',
          compStyle: comp.style || '温柔耐心',
          styleIndex: sIdx,
          compPersona: comp.persona || '',
          compGuidance: comp.guidance || '',

          llmMode: llm.mode || 'online',
          providerIndex: pIdx,
          llmModel: llm.model || 'deepseek-chat',
          llmBaseUrl: llm.base_url || 'https://api.deepseek.com/v1',
          llmApiKey: llm.api_key || '',

          ocrMode: ocr.mode || 'online',
          ocrModel: ocr.vision_model || 'gpt-4o-mini'
        });
      }
    } catch (err) {
      console.error('加载系统设置失败:', err);
    }
  },

  // Companion Handlers
  onCompNameInput(e) { this.setData({ compName: e.detail.value }); },
  onCompPersonaInput(e) { this.setData({ compPersona: e.detail.value }); },
  onCompGuidanceInput(e) { this.setData({ compGuidance: e.detail.value }); },
  onStyleChange(e) {
    const idx = parseInt(e.detail.value, 10);
    const style = this.data.styleOptions[idx];
    this.setData({ styleIndex: idx, compStyle: style });
  },
  applyPreset(e) {
    const type = e.currentTarget.dataset.type;
    const p = COMP_PRESETS[type];
    if (!p) return;
    let sIdx = this.data.styleOptions.indexOf(p.style);
    this.setData({
      compName: p.name,
      compStyle: p.style,
      styleIndex: sIdx >= 0 ? sIdx : 0,
      compPersona: p.persona,
      compGuidance: p.guidance
    });
    wx.showToast({ title: '已套用预设: ' + p.name, icon: 'none' });
  },

  // LLM Handlers
  onLlmModeChange(e) { this.setData({ llmMode: e.detail.value }); },
  onProviderChange(e) {
    const idx = parseInt(e.detail.value, 10);
    const p = PROVIDER_LIST[idx];
    this.setData({
      providerIndex: idx,
      llmBaseUrl: p.base || this.data.llmBaseUrl,
      llmModel: p.model || this.data.llmModel,
      ocrModel: p.vision || this.data.ocrModel
    });
  },
  onLlmModelInput(e) { this.setData({ llmModel: e.detail.value }); },
  onLlmBaseUrlInput(e) { this.setData({ llmBaseUrl: e.detail.value }); },
  onLlmApiKeyInput(e) { this.setData({ llmApiKey: e.detail.value }); },

  // OCR Handlers
  onOcrModeChange(e) { this.setData({ ocrMode: e.detail.value }); },
  onOcrModelInput(e) { this.setData({ ocrModel: e.detail.value }); },

  async testLlm() {
    this.setData({ testResult: '测速中...' });
    try {
      const res = await api.testLLM({
        base_url: this.data.llmBaseUrl,
        model: this.data.llmModel,
        api_key: this.data.llmApiKey
      });
      if (res && res.ok) {
        this.setData({ testResult: `✅ 正常 (${res.latency_ms}ms)` });
        wx.showToast({ title: '测试通过: ' + res.reply, icon: 'none' });
      } else {
        this.setData({ testResult: '❌ 连接失败' });
        wx.showToast({ title: res.error || '连接失败', icon: 'none' });
      }
    } catch (e) {
      this.setData({ testResult: '❌ 请求失败' });
    }
  },

  async saveAllSettings() {
    const selectedProvider = PROVIDER_LIST[this.data.providerIndex] ? PROVIDER_LIST[this.data.providerIndex].key : 'custom';
    const payload = {
      companion: {
        name: this.data.compName.trim(),
        style: this.data.compStyle,
        persona: this.data.compPersona.trim(),
        guidance: this.data.compGuidance.trim()
      },
      llm: {
        mode: this.data.llmMode,
        provider: selectedProvider,
        model: this.data.llmModel.trim(),
        base_url: this.data.llmBaseUrl.trim(),
        api_key: this.data.llmApiKey.trim()
      },
      ocr: {
        mode: this.data.ocrMode,
        vision_model: this.data.ocrModel.trim()
      }
    };

    try {
      const res = wx.getStorageSync('family_session_token')
        ? await api.saveCompanion(payload.companion) : await api.saveSettings(payload);
      if (res && res.ok) {
        wx.showToast({ title: '配置保存成功！', icon: 'success' });
        this.loadSettings();
      } else {
        wx.showToast({ title: res.error || '保存失败', icon: 'none' });
      }
    } catch (e) {
      wx.showToast({ title: '保存异常', icon: 'none' });
    }
  },

  onFamilyName(e) { this.setData({ familyName: e.detail.value }); },
  onFamilyPassword(e) { this.setData({ familyPassword: e.detail.value }); },
  onDeviceIdInput(e) { this.setData({ deviceIdInput: e.detail.value }); },
  async loginFamily() {
    try {
      const result = await api.familyLogin(this.data.familyName.trim(), this.data.familyPassword);
      wx.setStorageSync('family_session_token', result.token);
      this.setData({ familyLoggedIn: true, familyPassword: '', familyChildren: result.children || [] });
      this.loadReport(); this.loadFeatures(); this.loadSettings(); this.loadChildProfile();
      wx.showToast({ title: '家庭登录成功', icon: 'success' });
    } catch (_) { this.setData({ familyPassword: '' }); }
  },
  async loadFamily() {
    try { const result = await api.familyMe(); this.setData({ familyChildren: result.children || [], profileChildIndex: 0 }); await this.loadChildProfile(); }
    catch (_) { wx.removeStorageSync('family_session_token'); this.setData({ familyLoggedIn: false, familyChildren: [], profileName: '', profileGrade: 0, profileGender: 0, profileInterests: '' }); }
  },
  async logoutFamily() {
    try { await api.familyLogout(); } catch (_) {}
    wx.removeStorageSync('family_session_token');
    this.setData({ familyLoggedIn: false, familyChildren: [], issuedDeviceToken: '', profileName: '', profileGrade: 0, profileGender: 0, profileInterests: '' });
  },
  async loadChildProfile() {
    try {
      const id = this.data.familyLoggedIn ? this.data.familyChildren[this.data.profileChildIndex] : '';
      if (this.data.familyLoggedIn && !id) return;
      const { profile } = await api.getChildProfile(id);
      if (id !== (this.data.familyLoggedIn ? this.data.familyChildren[this.data.profileChildIndex] : '')) return;
      const gender = this.data.profileGenders.indexOf(profile.gender || '未设置');
      this.setData({ profileName: profile.name || '', profileGrade: profile.grade || 0,
        profileGender: Math.max(0, gender), profileInterests: profile.interests || '' });
    } catch (_) {}
  },
  onProfileChild(e) { this.setData({ profileChildIndex: Number(e.detail.value), profileName: '', profileGrade: 0, profileGender: 0, profileInterests: '' }); this.loadChildProfile(); },
  onProfileName(e) { this.setData({ profileName: e.detail.value }); },
  onProfileGrade(e) { this.setData({ profileGrade: Number(e.detail.value) }); },
  onProfileGender(e) { this.setData({ profileGender: Number(e.detail.value) }); },
  onProfileInterests(e) { this.setData({ profileInterests: e.detail.value }); },
  async saveChildProfile() {
    const id = this.data.familyLoggedIn ? this.data.familyChildren[this.data.profileChildIndex] : '';
    if (this.data.familyLoggedIn && !id) return;
    try {
      await api.saveChildProfile({ name: this.data.profileName.trim(), grade: this.data.profileGrade,
        gender: this.data.profileGender ? this.data.profileGenders[this.data.profileGender] : '',
        interests: this.data.profileInterests.trim() }, id);
      wx.showToast({ title: '资料已保存', icon: 'success' });
    } catch (_) { wx.showToast({ title: '保存失败', icon: 'none' }); }
  },
  async issueDeviceToken() {
    if (!this.data.familyChildren.length || !this.data.deviceIdInput.trim()) return;
    try {
      const result = await api.issueDevice(this.data.familyChildren[0], this.data.deviceIdInput.trim());
      this.setData({ issuedDeviceToken: result.device_token });
    } catch (_) {}
  },
  async revokeDeviceToken() {
    if (!this.data.familyChildren.length || !this.data.deviceIdInput.trim()) return;
    try {
      const result = await api.revokeDevice(this.data.familyChildren[0], this.data.deviceIdInput.trim());
      this.setData({ issuedDeviceToken: '' });
      wx.showToast({ title: result.ok ? '设备令牌已撤销' : '设备未找到', icon: 'none' });
    } catch (_) {}
  },
  clearIssuedToken() { this.setData({ issuedDeviceToken: '' }); },
  onScreenTokenInput(e) { this.setData({ screenTokenInput: e.detail.value }); },
  saveScreenToken() {
    const token = this.data.screenTokenInput.trim();
    if (token) wx.setStorageSync('screen_admin_token', token);
    else wx.removeStorageSync('screen_admin_token');
    this.setData({ screenTokenInput: '', hasScreenToken: !!token });
    wx.showToast({ title: token ? '令牌已保存' : '令牌已清除', icon: 'none' });
  },
  onBaseUrlInput(e) { this.setData({ baseUrl: e.detail.value }); },
  saveBaseUrl() {
    const url = this.data.baseUrl.trim();
    if (!url) {
      wx.showToast({ title: '请输入有效地址', icon: 'none' });
      return;
    }
    if (app && app.globalData) app.globalData.baseUrl = url;
    wx.setStorageSync('custom_base_url', url);
    wx.showToast({ title: '地址已保存', icon: 'success' });
    this.loadReport();
    this.loadSettings();
  }
});
