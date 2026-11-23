import {request} from './api/client.js';
import {state, setSession} from './stores/session.js';
import {roleLabels} from './constants/workflow.js';
import {topic} from './constants/project.js';
import {loginPage} from './pages/login.js';
import {casesPage} from './pages/cases.js';
import {bindEditor, editorPage} from './pages/editor.js';
import {plansPage, bindPlans} from './pages/plans.js';
import {runsPage} from './pages/runs.js';
import {bindEvidence, evidencePage} from './pages/evidence.js';
import {go, route} from './router/hash.js';
import {submit} from './hooks/submit.js';
import {esc} from './utils/dom.js';

const app = document.getElementById('app');
document.title = topic.name;
let revision = 0;
function fail(error) {
  if (error.status===401 && state.session) { setSession(null); void refresh(); return; }
  const message = document.getElementById('message');
  if (message) { message.textContent=error.message; message.className='error'; }
}
const api = (path, options={})=>request(path, {...options, token:state.session?.token});
const pages = {cases:casesPage, editor:editorPage, plans:plansPage, runs:runsPage, evidence:evidencePage};
const titles = {cases:topic.modules[0], editor:topic.modules[1], plans:'方案预览', runs:topic.modules[2], evidence:topic.modules[3]};
async function refresh() {
  const key = ++revision;
  if (!state.session) {
    app.innerHTML=loginPage();
    submit('login-form', async data=>{
      setSession(await api('/login', {method:'POST', data:Object.fromEntries(data)}));
      if (location.hash!=='#cases') go('cases');
      else await refresh();
    }, fail);
    return;
  }
  const {key: page,id} = route();
  if (!pages[page]) { go('cases'); return; }
  try {
    const result = await Promise.all([state.spec || api('/spec'),api('/cases'),api('/plans'),api('/runs'),api('/evidence'),api('/audit')]);
    if (revision !== key) return;
    [state.spec,state.cases,state.plans,state.runs,state.evidence,state.audit]=result;
  } catch (error) {
    if (revision !== key) return;
    if (!document.getElementById('message')) app.innerHTML='<main><div id="message" role="alert"></div><button id="retry">重试</button></main>';
    document.getElementById('retry')?.addEventListener('click', refresh);
    fail(error);
    return;
  }
  const navigation = Object.keys(pages).map(x=>x==='editor'&&state.session.role!=='operator' ? ''
    : `<a href="#${x}" ${x===page?'aria-current="page"':''}>${esc(titles[x])}</a>`).join('');
  app.innerHTML=`<header class="masthead"><a class="identity" href="#cases">${esc(topic.name)}</a><div class="user"><span>${esc(roleLabels[state.session.role])}</span><button id="logout" class="secondary">退出</button></div></header>
    <div class="workspace"><nav aria-label="业务模块">${navigation}</nav><main><div id="message" role="alert"></div>${pages[page](state,id)}</main></div>`;
  document.getElementById('logout').addEventListener('click',()=>{setSession(null); void refresh();});
  if (page==='editor') bindEditor(state, api, fail);
  if (page==='plans') bindPlans(state, api, refresh, fail);
  if (page==='evidence') bindEvidence(api, refresh, fail);
}
window.addEventListener('hashchange', refresh);
void refresh();
