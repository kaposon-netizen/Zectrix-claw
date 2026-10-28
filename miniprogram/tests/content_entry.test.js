const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
let page, last;
const api = new Proxy({}, {get(_, name){return async (...args)=>{last={name,args};if(name==='getCustomPoems')return {poems:[]};if(name==='getCustomIdioms')return {idioms:[]};return {ok:true}}}});
const wx={showToast(){},showLoading(){},hideLoading(){}};
vm.runInNewContext(fs.readFileSync(require('path').join(__dirname,'../pages/content_entry/content_entry.js'),'utf8'),{Page(p){page=p},require(){return api},wx,Error});
page.setData = values => Object.assign(page.data, values);
(async()=>{
 page.data.index=0;page.data.body='语文：完成练习';await page.save();assert.strictEqual(last.name,'quickInputTasks');
 page.data.index=3;page.data.title='测试古诗';page.data.body='第一句\n第二句';page.data.extra='作者';await page.save();assert.strictEqual(last.name,'addCustomPoem');assert.strictEqual(last.args[0].lines.length,2);
 page.data.index=7;page.data.title='测试活动';page.data.body='内容';page.data.extra='上海';await page.save();assert.strictEqual(last.name,'addNewsItem');assert.strictEqual(last.args[0].region,'上海');
 page.data.index=11;page.data.title='3+4';page.data.body='7';await page.save();assert.strictEqual(last.name,'addCustomArith');
 page.data.index=12;page.data.title='关键词';page.data.body='另一词';page.data.extra='真';await page.save();assert.strictEqual(last.name,'addCustomFact');assert.strictEqual(last.args[0].is_true,true);
 page.data.index=13;page.data.title='春';page.data.body='春眠不觉晓';await page.save();assert.strictEqual(last.name,'addFeihua');
 page.data.index=14;page.data.title='去公园';page.data.body='20';await page.save();assert.strictEqual(last.name,'addWish');assert.strictEqual(last.args[1],20);
 console.log('Mini program manual entry checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
