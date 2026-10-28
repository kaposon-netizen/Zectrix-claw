const api = require('../../utils/api.js');
const types = [
  {name:'作业',title:'标题（可不填）',body:'作业内容，每行一项'},
  {name:'习惯',title:'习惯名称',body:'备注（可不填）'},
  {name:'生字词',title:'词单名称',body:'生字词，每行或用逗号分隔'},
  {name:'古诗',title:'诗名',body:'诗句，每行一句',extra:'作者'},
  {name:'成语',title:'成语',body:'释义',extra:'拼音（可不填）'},
  {name:'英语生词',title:'单词',body:'中文释义'},
  {name:'英语短文',title:'短文标题',body:'每行一句，格式：英文 || 中文'},
  {name:'小学新鲜事',title:'活动标题',body:'活动说明',extra:'地区（可不填）'},
  {name:'错题',title:'题目',body:'正确答案',extra:'学科（如数学）'},
  {name:'费曼主题',title:'主题名称',body:'提示（可不填）'},
  {name:'猜猜看',title:'谜底',body:'提示',extra:'分类（可不填）'},
  {name:'口算题',title:'算式',body:'答案',extra:'提示（可不填）'},
  {name:'事实核查',title:'关键词一',body:'关键词二',extra:'家长确认：填写“真”或“假”'},
  {name:'飞花令',title:'令字',body:'诗句'},
  {name:'心愿',title:'心愿名称',body:'需要的星星数量'},
  {name:'亲子留言',title:'标题（可不填）',body:'留言内容'},
  {name:'倒计时',title:'事项名称',body:'日期 YYYY-MM-DD',extra:'鼓励语（可不填）'}
];
Page({
  data:{names:types.map(t=>t.name),index:0,type:types[0],title:'',body:'',extra:'',vocabEntries:[],busy:false},
  onLoad(options){const index=Number(options.index);if(Number.isInteger(index)&&index>=0&&index<types.length)this.setData({index,type:types[index]})},
  changeType(e){let index=Number(e.detail.value);this.setData({index,type:types[index],title:'',body:'',extra:'',vocabEntries:[]})},
  onTitle(e){this.setData({title:e.detail.value})}, onBody(e){this.setData({body:e.detail.value})}, onExtra(e){this.setData({extra:e.detail.value})},
  onVocabEntry(e){const rows=this.data.vocabEntries.slice();const i=Number(e.currentTarget.dataset.index), field=e.currentTarget.dataset.field;if(rows[i]&&['word','meaning','example','example_cn'].includes(field)){rows[i]={...rows[i],[field]:e.detail.value};this.setData({vocabEntries:rows})}},
  choosePhoto(){
    if(this.data.busy)return;
    const before={index:this.data.index,title:this.data.title,body:this.data.body,extra:this.data.extra,vocabEntries:JSON.stringify(this.data.vocabEntries)};
    wx.chooseMedia({count:1,mediaType:['image'],sourceType:['camera','album'],success:r=>{
      const file=r.tempFiles&&r.tempFiles[0];if(!file)return;
      if(file.size>8*1024*1024){wx.showToast({title:'照片不能超过 8 MB',icon:'none'});return}
      wx.getFileSystemManager().readFile({filePath:file.tempFilePath,encoding:'base64',success:async d=>{
        this.setData({busy:true});wx.showLoading({title:'识别中'});
        try{const result=await api.ocrText(d.data,types[before.index].name);if(before.index!==this.data.index||before.title!==this.data.title||before.body!==this.data.body||before.extra!==this.data.extra||before.vocabEntries!==JSON.stringify(this.data.vocabEntries)){wx.showToast({title:'内容已修改，请重新识别',icon:'none'});return}const f=result.fields||{};this.setData({title:f.title||'',body:f.body||'',extra:f.extra||'',vocabEntries:before.index===5?(f.entries||[]):[]});wx.showToast({title:'请核对识别内容',icon:'none'})}
        catch(_){wx.showToast({title:'识别失败',icon:'none'})}
        finally{wx.hideLoading();this.setData({busy:false})}
      },fail:()=>wx.showToast({title:'无法读取图片',icon:'none'})})
    }});
  },
  async save(){
    const index=this.data.index,title=this.data.title.trim(),body=this.data.body.trim(),extra=this.data.extra.trim();
    const vocabEntries=index===5?this.data.vocabEntries.filter(x=>x.word.trim()):[];
    if(!vocabEntries.length&&(([0,15].includes(index)&&!body)||(![0,15].includes(index)&&!title)||([2,3,4,5,6,7,8,10,11,12,13,14,16].includes(index)&&!body))){wx.showToast({title:'请填写必要内容',icon:'none'});return}
    this.setData({busy:true});
    try{
      let result;
      if(index===0) result=await api.quickInputTasks(body,false);
      else if(index===1) result=await api.addHabit(title);
      else if(index===2) result=await api.setDictation(body,title);
      else if(index===3){let old=await api.getCustomPoems();if((old.poems||[]).some(x=>x.title===title))throw Error('已有同名古诗，请到原列表编辑');result=await api.addCustomPoem({title,author:extra,lines:body.split(/\n+/).map(s=>s.trim()).filter(Boolean)})}
      else if(index===4){let old=await api.getCustomIdioms();if((old.idioms||[]).some(x=>x.idiom===title))throw Error('已有同名成语，请到原列表编辑');result=await api.addCustomIdiom({idiom:title,pinyin:extra,meaning:body})}
      else if(index===5){if(vocabEntries.length){for(const row of vocabEntries){result=await api.addVocab(row.word.trim(),row.meaning.trim(),row.example.trim(),row.example_cn.trim());if(!result||!result.ok)throw Error('部分词条保存失败，请核对后重试')}}else result=await api.addVocab(title,body)}
      else if(index===6){let sentences=body.split(/\n+/).map(s=>s.split('||').map(x=>x.trim())).filter(s=>s.length>=2&&s[0]&&s[1]);if(!sentences.length)throw Error('请按英文 || 中文分行填写');result=await api.addCustomReading({title,sentences})}
      else if(index===7) result=await api.addNewsItem({title,summary:body,content:body,region:extra||'全国'});
      else if(index===8) result=await api.addMistake({question:title,expected:body,category:extra||'数学'});
      else if(index===9) result=await api.addFeynmanTopic({topic:title,hint:body});
      else if(index===10) result=await api.addCustomGuess({name:title,hint:body,category:extra||'综合'});
      else if(index===11) result=await api.addCustomArith({expr:title,ans:body,hint:extra});
      else if(index===12){if(!['真','假'].includes(extra))throw Error('请由家长填写真或假');result=await api.addCustomFact({kw1:title,kw2:body,is_true:extra==='真'})}
      else if(index===13) result=await api.addFeihua({keyword:title,verse:body});
      else if(index===14){if(!/^\d+$/.test(body)||Number(body)<1||Number(body)>100000)throw Error('请填写有效星数');result=await api.addWish(title,Number(body))}
      else if(index===15) result=await api.sendMessage(body);
      else if(index===16){if(!/^\d{4}-\d{2}-\d{2}$/.test(body))throw Error('日期格式应为 YYYY-MM-DD');result=await api.addCountdown(title,body,extra)}
      if(!result||!result.ok)throw Error('保存失败');
      this.setData({title:'',body:'',extra:'',vocabEntries:[]});wx.showToast({title:'已保存',icon:'success'});
    }catch(e){wx.showToast({title:e.message||'保存失败',icon:'none'})}
    finally{this.setData({busy:false})}
  }
});
