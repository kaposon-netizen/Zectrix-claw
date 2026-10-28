// app.js
App({
  globalData: {
    // 默认连接局域网 PocketClaw 伴学服务端（按实际网络环境配置）
    baseUrl: 'http://127.0.0.1:8000',
    childId: 'kid',
    deviceOnline: false,
    battery: null
  },
  onLaunch() {
    console.log('PocketClaw WeChat Mini-Program launched.');
  }
})
