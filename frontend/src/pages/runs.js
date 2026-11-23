import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {planStatusLabels} from '../constants/workflow.js';
import {topic} from '../constants/project.js';
import {date, esc} from '../utils/dom.js';

function planBadge(status) {
  return `<span class="status" data-state="${esc(status)}">${esc(planStatusLabels[status] || status)}</span>`;
}

const ACTION_LABELS = {
  preview_plan: '预览方案', apply_plan: '确认生效', stale_plan: '方案失效',
  fail_plan: '生效失败', review: '签署证据', update_case: '调整源数据', create_case: '建立档案',
};

export function runsPage(state, identity) {
  const plans = state.plans;
  const current = plans.find(row=>row.id===identity) || plans[0];
  const caseNames = new Map(state.cases.map(row=>[row.id, row.title]));
  const planRows = plans.map(row=>[`<a href="#plans/${row.id}">v${row.version}</a>`, esc(row.cutoff),
    planBadge(row.status), esc(caseNames.get(row.case_id) || row.case_id), esc(date(row.created_at))]);
  const evidenceByPlan = new Map(state.evidence.filter(row=>row.plan_id).map(row=>[row.plan_id, row]));
  const detail = current ? (()=>{
    const proof = evidenceByPlan.get(current.id);
    return `<h2>方案 v${current.version}</h2>
      <p data-testid="plan-status">${planBadge(current.status)} · 截止日 ${esc(current.cutoff)}</p>
      <dl><dt>建立时间</dt><dd>${esc(date(current.created_at))}</dd><dt>生效时间</dt><dd>${esc(date(current.applied_at))}</dd></dl>
      ${current.rejection_reason ? `<p class="error">${esc(current.rejection_reason)}</p>` : ''}
      ${current.error ? `<p class="error">${esc(current.error)}</p>` : ''}
      ${proof ? `<a class="button" href="#evidence/${proof.id}">查看已签证据</a>` : '<p class="note">尚未生成证据。</p>'}`;
  })() : '<h2>生命周期执行</h2><p>预览并确认方案后，可在此查看每个版本的执行状态。</p>';
  const events = state.audit.filter(row=>row.action!=='login').slice(0, 30);
  return `${heading(topic.modules[2], '按方案版本查看预览、失效、生效与证据签署的先后顺序。')}
    <div class="spread"><section>${records(['版本','截止日','状态','档案','建立时间'], planRows, '还没有方案。先建立档案并预览截止日调整。')}</section><aside class="brief">${detail}</aside></div>
    ${events.length?panel('操作记录', records(['操作者','操作','时间'], events.map(x=>[esc(x.actor),esc(ACTION_LABELS[x.action] || x.action),esc(date(x.created_at))]))):''}`;
}
