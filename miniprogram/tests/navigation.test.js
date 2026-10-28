const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const config = JSON.parse(fs.readFileSync(path.join(root, 'app.json')));
const tabs = config.tabBar.list.map(item => '/' + item.pagePath);
assert.deepStrictEqual(tabs, ['/pages/index/index', '/pages/practice/practice', '/pages/wisdom/wisdom', '/pages/report/report']);
for (const page of config.pages) {
  for (const ext of ['.js', '.json', '.wxml', '.wxss']) {
    assert(fs.existsSync(path.join(root, page + ext)), `${page}${ext} missing`);
  }
}
const app = { globalData: {} };
const calls = [];
function loadPage(file) {
  let definition;
  const context = {
    Page(value) { definition = value; }, getApp() { return app; },
    require() { return {}; }, console,
    wx: { switchTab(opts) { assert(tabs.includes(opts.url)); calls.push(opts.url); },
          navigateTo(opts) { assert(!tabs.includes(opts.url)); calls.push(opts.url); } }
  };
  vm.runInNewContext(fs.readFileSync(path.join(root, file), 'utf8'), context, { filename: file });
  return definition;
}
const index = loadPage('pages/index/index.js');
index.goToTasks(); index.goToHabits(); index.goToTimetable();
index.goToPractice({ currentTarget: { dataset: { tab: 'dictation' } } });
assert.strictEqual(app.globalData.practiceTab, 'dictation');
const practice = loadPage('pages/practice/practice.js');
practice.setData = value => Object.assign(practice.data, value);
practice.loadAll = () => {};
practice.onShow();
assert.strictEqual(practice.data.tab, 'dictation');
assert.strictEqual(app.globalData.practiceTab, '');
practice.chooseReadingPhoto();
assert.strictEqual(calls[calls.length - 1], '/pages/content_entry/content_entry?index=6');
practice.openEntry({currentTarget:{dataset:{index:5}}});
assert.strictEqual(calls[calls.length - 1], '/pages/content_entry/content_entry?index=5');
const wisdom = loadPage('pages/wisdom/wisdom.js');
wisdom.openNews();
practice.onShow();
assert.strictEqual(practice.data.tab, 'news');
const report = loadPage('pages/report/report.js');
report.openTasks(); report.openHabits(); report.openTimetable();
assert(calls.length >= 8);
console.log('Mini program navigation checks passed');
