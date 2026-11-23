import {heading} from '../components/common/heading.js';
import {fields, readFields} from '../components/fields.js';
import {topic} from '../constants/project.js';
import {esc} from '../utils/dom.js';
import {submit} from '../hooks/submit.js';
import {go} from '../router/hash.js';

export function editorPage(state, identity) {
  if (state.session.role!=='operator') return heading(topic.modules[1], '经办员可以建立并提交业务档案。');
  const original = state.cases.find(row=>row.id===identity);
  const payload = original?.payload || state.spec.sample;
  state.draft = payload;
  const latest = original ? state.plans.find(row=>row.case_id===original.id) : null;
  const latestLine = latest ? `<p class="note">当前方案 v${latest.version} · 保留截止日 ${esc(latest.cutoff)}。修改源数据后原方案将失效，需重新预览。</p>` : '';
  return `${heading(topic.modules[1], state.spec.guidance)}
    <form id="case-form"><label class="field title-field"><span>档案名称</span><input name="title" required maxlength="120" value="${esc((original?.title || state.spec.name) + (original ? '' : ' · 复核'))}" /></label>
    ${latestLine}
    <div class="field-grid">${fields(payload, state.spec.field_labels)}</div><div class="actions form-actions"><button>保存并预览方案</button><a class="secondary" href="#cases">返回档案</a></div></form>`;
}

export function bindEditor(state, api, fail) {
  submit('case-form', async data=>{
    const payload = readFields(state.draft, state.spec.field_labels);
    const original = state.cases.find(row=>row.id===Number(location.hash.split('/')[1]));
    let saved;
    if (original) {
      saved = await api(`/cases/${original.id}`, {method:'PUT', data:{title:data.get('title'), payload}});
    } else {
      saved = await api('/cases', {method:'POST', data:{title:data.get('title'), payload}});
    }
    try {
      const plan = await api(`/cases/${saved.id}/plans`, {method:'POST'});
      go(`plans/${plan.id}`);
    } catch (error) {
      go(`cases/${saved.id}`);
      fail(new Error(`档案已保存。方案预览失败：${error.message}`));
    }
  }, fail);
}
