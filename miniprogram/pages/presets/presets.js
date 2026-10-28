const api = require('../../utils/api.js');
Page({
  data: { categories:['生字词','成语','古诗','英语生词'], keys:['words','idioms','poems','english'], category:0,
    grades:['全部年级','不选年级','一年级','二年级','三年级','四年级','五年级','六年级'], gradeValues:['all','none','1','2','3','4','5','6'], grade:0, items:[], selected:[] },
  onShow(){this.load()},
  onCategory(e){this.setData({category:Number(e.detail.value),selected:[]});this.load()},
  onGrade(e){this.setData({grade:Number(e.detail.value),selected:[]});this.load()},
  onSelected(e){const selected=e.detail.value;this.setData({selected,items:this.data.items.map(x=>({...x,checked:selected.includes(x.id)}))})},
  selectAll(){this.setData({selected:this.data.items.map(x=>x.id),items:this.data.items.map(x=>({...x,checked:true}))})},
  selectNone(){this.setData({selected:[],items:this.data.items.map(x=>({...x,checked:false}))})},
  async load(){
    const category=this.data.keys[this.data.category], grade=this.data.gradeValues[this.data.grade];
    try{const result=await api.getGradePresets(category,grade);if(category!==this.data.keys[this.data.category]||grade!==this.data.gradeValues[this.data.grade])return;this.setData({items:result.items||[],selected:[]})}
    catch(_){wx.showToast({title:'预设内容加载失败',icon:'none'})}
  },
  async importSelected(){
    if(!this.data.selected.length){wx.showToast({title:'请先选择内容',icon:'none'});return}
    try{const result=await api.importGradePresets(this.data.keys[this.data.category],this.data.selected);wx.showToast({title:'已导入 '+result.imported+' 项',icon:'none'})}
    catch(_){wx.showToast({title:'导入失败',icon:'none'})}
  }
});
