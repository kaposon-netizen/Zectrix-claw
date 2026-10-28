const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
let token = '', url = 'http://example.test', calls = [];
const app = { globalData: { baseUrl: url } };
const wx = {
  getStorageSync(key) { return key === 'family_session_token' ? token : ''; },
  showToast() {},
  request(req) { calls.push(req); req.success({ statusCode: 200, data: { ok: true } }); }
};
const moduleObj = { exports: {} };
vm.runInNewContext(fs.readFileSync('miniprogram/utils/api.js', 'utf8'), { module: moduleObj, getApp: () => app, wx, console, Promise });
const api = moduleObj.exports;
(async () => {
  await assert.rejects(api.familyLogin('family', 'password'));
  assert.strictEqual(calls.length, 0);
  app.globalData.baseUrl = 'https://example.test';
  await api.familyLogin('family', 'password');
  assert.strictEqual(calls.length, 1);
  token = 'temporary-test-token';
  await api.getTasks();
  assert.strictEqual(calls[1].header.Authorization, 'Bearer ' + token);
  app.globalData.baseUrl = 'http://example.test';
  await assert.rejects(api.getTasks());
  assert.strictEqual(calls.length, 2);
  console.log('Mini program credential transport checks passed');
})().catch(err => { console.error(err); process.exitCode = 1; });
