import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from app import create_app
from pocketclaw.endpoint import _HTML_PARENT_PORTAL


def test_arith_settings_saved_in_isolated_data_dir(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        data = {'grade': 3, 'count': 20, 'op_type': 'mul_div', 'push': True}
        result = client.post('/api/pocketclaw/mp/arith', json=data)
        assert result.status_code == 200
        assert result.json()['config'] == {k: data[k] for k in ('grade', 'count', 'op_type')}
        assert result.json()['pushed'] == 0
        assert client.get('/api/pocketclaw/mp/arith').json()['config'] == result.json()['config']


def test_parent_arith_buttons_update_selection_and_save(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('requires Node.js')
    html = _HTML_PARENT_PORTAL
    for name in ('arithGradeGroup', 'arithCountGroup', 'arithOpGroup'):
        assert f'id="{name}"' in html
    script = re.search(r'function selectArithPill\(.*?(?=async function addManualMistake)', html, re.S).group()
    test_js = r'''
const assert = require('assert');
const groups = {};
for (const [id, values] of Object.entries({arithGradeGroup:['1','2','3','4'],arithCountGroup:['5','10','20'],arithOpGroup:['all','add_sub','mul_div']})) {
  groups[id] = values.map(val => ({dataset:{val}, active:false, classList:{toggle(cls, on){this.owner.active=on}}}));
  groups[id].forEach(b => b.classList.owner=b);
}
const document = {querySelectorAll(selector){return groups[selector.split(' ')[0].slice(1)]}};
let state={arithGrade:2,arithCount:10,arithOp:'all',arithDirty:false,arithRevision:0};
let saved, toast, status=200;
async function fetch(url, options){saved=JSON.parse(options.body);return {ok:status===200,json:async()=>({ok:status===200,pushed:0})}}
function showToast(s){toast=s}
async function loadOverview(){}
''' + script + r'''
(async()=>{
  selectArithPill('grade',3);selectArithPill('count',20);selectArithPill('op','mul_div');
  assert.deepStrictEqual(Object.values(groups).map(g=>g.filter(b=>b.active).map(b=>b.dataset.val)),[['3'],['20'],['mul_div']]);
  assert(state.arithDirty);
  await saveArithConfig();
  assert.deepStrictEqual(saved,{grade:3,count:20,op_type:'mul_div',push:true});
  assert(!state.arithDirty && toast.includes('已保存'));
  selectArithPill('grade',4);status=500;await saveArithConfig();
  assert(state.arithDirty && toast.includes('保存失败'));
})().catch(e=>{console.error(e);process.exit(1)});
'''
    path = tmp_path / 'arith-test.js'
    path.write_text(test_js)
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True)
