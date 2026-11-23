import {heading, panel} from '../components/common/heading.js';
import {entries, records} from '../components/common/records.js';
import {planStatusLabels} from '../constants/workflow.js';
import {topic} from '../constants/project.js';
import {date, el, esc} from '../utils/dom.js';

function planBadge(status) {
  return `<span class="status" data-state="${esc(status)}">${esc(planStatusLabels[status] || status)}</span>`;
}

export function plansPage(state, identity) {
  const operator = state.session.role === 'operator';
  const plans = state.plans;
  const current = plans.find(row=>row.id===identity) || plans[0];
  const caseNames = new Map(state.cases.map(row=>[row.id, row.title]));
  if (!current) {
    return `${heading(topic.modules[1], '调整保留截止日之前，先预览会影响的分区目录、在线凭条和已摘除凭条，再确认生效。', operator ? '<a class="button" href="#editor">建立档案</a>' : '')}
      <p class="empty">还没有方案。经办员在档案中预览保留截止日调整后，会在此列出。</p>`;
  }
  const preview = current.preview;
  const summary = preview.summary;
  const metrics = Object.entries(summary).map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');
  const rejection = current.rejection_reason ? `<div class="error" data-testid="rejection">失效原因：${esc(current.rejection_reason)}</div>` : '';
  const failure = current.error ? `<div class="error" data-testid="failure">${esc(current.error)}</div>` : '';
  const actions = [`<button id="plan-download" data-id="${current.id}">导出方案</button>`];
  if (operator && ['previewed','failed'].includes(current.status)) {
    actions.push(`<button id="plan-apply" data-id="${current.id}" ${current.status==='previewed'?'':'class="secondary"'}>确认生效</button>`);
  }
  const evidence = current.status==='applied' ? state.evidence.find(row=>row.plan_id===current.id) : null;
  const appliedLine = evidence ? `<p data-testid="plan-evidence">已签证据 <a href="#evidence/${evidence.id}">证据 ${evidence.id}</a> · ${esc(evidence.reviewer || '待复核')}</p>` : '';
  const historyRows = plans.map(row=>[`<a href="#plans/${row.id}">v${row.version}</a>`, esc(row.cutoff), planBadge(row.status),
    esc(caseNames.get(row.case_id) || row.case_id), esc(date(row.created_at))]);
  const sections = [
    panel('影响明细 · 分区目录', entries(preview.partitions)),
    panel('影响明细 · 在线凭条', entries(preview.online)),
    panel('影响明细 · 已摘除凭条', entries(preview.archived)),
  ].join('');
  return `${heading(`方案 v${current.version} · ${caseNames.get(current.case_id) || '档案'}`,
      `保留截止日 ${current.cutoff}。${planStatusLabels[current.status] || current.status}。`, `<div class="actions">${actions.join('')}</div>`)}
    ${rejection}${failure}
    <div class="spread evidence-top"><dl class="metrics">${metrics}</dl><aside class="brief"><h2>方案版本</h2>
      <p>版本 v${current.version} · ${planBadge(current.status)}</p>
      <p class="note">预览校验码</p><code class="digest">${esc(current.preview_digest)}</code>
      ${appliedLine}
      ${current.baseline_plan_id ? `<p class="note">基于上一版方案 ${esc(current.baseline_plan_id)} 的已签证据。</p>` : '<p class="note">首个方案，无已签证据基线。</p>'}
    </aside></div>
    ${sections}
    <details open><summary>方案历史</summary>${records(['版本','截止日','状态','档案','建立时间'], historyRows)}</details>`;
}

export function bindPlans(state, api, update, fail) {
  const apply = document.getElementById('plan-apply');
  if (apply) apply.addEventListener('click', async()=>{
    apply.disabled = true;
    try {
      await api(`/plans/${apply.dataset.id}/apply`, {method:'POST'});
      await update();
    } catch (error) { fail(error); await update(); }
    finally { apply.disabled = false; }
  });
  const button = document.getElementById('plan-download');
  if (!button) return;
  button.addEventListener('click', async()=>{
    try {
      const response = await api(`/plans/${button.dataset.id}/download`, {response:true});
      const url = URL.createObjectURL(await response.blob());
      const link = el('a');
      link.href = url;
      link.download = `plan-${button.dataset.id}.json`;
      link.click();
      setTimeout(()=>URL.revokeObjectURL(url), 1000);
    } catch (error) { fail(error); }
  });
}
