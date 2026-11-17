import {state} from '../stores/session.js';
import {esc} from '../utils/html.js';
import {badge} from '../components/common/status.js';
import {select} from '../components/common/fields.js';
import {time} from '../utils/format.js';

const STATUS_TEXT={queued:'待处理',running:'计算中',done:'已完成',failed:'失败'};
const fmtEps=n=>Number(n).toFixed(6).replace(/0+$/,'').replace(/\.$/,'');

function budgetCard(b){
 const total=Number(b.total_epsilon),used=Number(b.used_epsilon),left=Number(b.remaining_epsilon),per=Number(b.epsilon_per_release);
 const ratio=total>0?Math.min(100,used/total*100):100;
 const state=left<=0?'耗尽':left<per?'不足以再领取':'可领取';
 return `<article class="experiment"><div class="eyebrow">${esc(b.code)} · 预算 ${state}</div><h3>${esc(b.name)}</h3>
 <div class="meta">总预算 ε=${fmtEps(total)} · 单次额度 ε=${fmtEps(per)}</div>
 <div class="budgetbar" role="img" aria-label="已用 ${ratio.toFixed(0)}%"><span style="width:${ratio}%"></span></div>
 <div class="meta">已用 <strong>${fmtEps(used)}</strong> / 剩余 <strong>${fmtEps(left)}</strong> · 已领取 ${b.releases_charged} 次</div></article>`;
}

function releaseRow(r,code){
 const canRetry=r.status==='failed';
 const done=r.status==='done';
 return `<tr>
 <td>#${r.id}<br><span class="meta">${esc(r.sample_ref)}</span></td>
 <td>${esc(code)}</td>
 <td>${badge(STATUS_TEXT[r.status]||r.status)}</td>
 <td>ε=${fmtEps(r.epsilon)}</td>
 <td>${time(r.created_at)}<br><span class="meta">${r.completed_at?'完成 '+time(r.completed_at):'—'}</span></td>
 <td>${r.error?`<span class="error">${esc(r.error)}</span>`:(done?'—':'等待后台 worker')}</td>
 <td class="actions">
  <button data-privacy-open="${r.id}">查看</button>
  ${canRetry&&state.user.role!=='viewer'?`<button data-privacy-retry="${r.id}">重试</button>`:''}
  ${done?(state.user.role==='viewer'?`<button data-privacy-export="${r.id}">下载汇总</button>`:`<button data-privacy-download="${r.id}">外发并下载</button>`):''}
 </td></tr>`;
}

function detail(){
 const r=state.selectedRelease;
 if(!r)return '<p class="notice">在下方任务中选择一条已领取记录，查看加噪结果与可用性提示。</p>';
 const res=r.result;
 if(!res)return `<p class="notice">任务 #${r.id}（${esc(r.sample_ref)}）${esc(STATUS_TEXT[r.status]||r.status)}。${esc(r.error||'后台处理完成后刷新即可查看。')}</p>`;
 const c=res.counts,a=res.availability,hw=res.noise.error_half_width;
 return `<div class="eyebrow">任务 #${r.id} · 样本 ${esc(r.sample_ref)} · ε=${fmtEps(res.noise.epsilon)}</div>
 <div class="scroll"><table class="dpcounts"><thead><tr><th>组别</th><th>曝光人数（加噪）</th><th>激活人数（加噪）</th><th>95% 误差</th></tr></thead><tbody>
 <tr><td>A · 原引导</td><td>${c.exposed_a}</td><td>${c.activated_a}</td><td>±${hw} 人</td></tr>
 <tr><td>B · 新引导</td><td>${c.exposed_b}</td><td>${c.activated_b}</td><td>±${hw} 人</td></tr>
 </tbody></table></div>
 <p class="meta">按参与者去重，每人每项最多贡献 1；激活人数已限制为曝光人数的子集。四个计数彼此独立加噪，不可据此反推个人。</p>
 <p class="notice ${a.usable?'good':'warning'}">${a.usable?'可用性提示：'+esc(a.hint):a.reasons.map(esc).join('；')}</p>
 ${a.notes.length?`<p class="meta">${a.notes.map(esc).join('；')}</p>`:''}
 <p class="meta">机制：${esc(res.noise.mechanism)} · 每统计量 ε=${fmtEps(res.noise.per_statistic_epsilon)} · 敏感度 ${res.noise.sensitivity} · 尺度 b=${res.noise.scale}<br>${esc(res.noise.error_note)}<br>${esc(res.noise.contribution_limit)}</p>
 <p class="notice">${esc(res.notice)}</p>`;
}

export function render(){
 const budgets=state.budgets||[],releases=state.releases||[];
 const codes=Object.fromEntries((state.experiments||[]).map(e=>[e.id,e.code]));
 const claimable=budgets.filter(b=>Number(b.remaining_epsilon)>=Number(b.epsilon_per_release));
 return `<div class="split"><section><h2>只发布加噪人数，不发布个人明细</h2>
 <p class="notice">渠道分享使用差分隐私汇总：按参与者聚合曝光与激活，限定每人贡献，Laplace 噪声加在人数上。结果只含加噪人数、噪声机制、误差范围与可用性提示；不含身份或原始事件，也不作为内部上线裁决依据。</p>
 <h3>预算与额度</h3>
 <div class="budget-list">${budgets.map(budgetCard).join('')||'<p class="notice">暂无实验预算。</p>'}</div>
 <h3>发布任务</h3>
 <p class="meta">待处理 → 计算中 → 已完成 / 失败；失败可重试，重试沿用原扣费与原噪声。同一冻结样本重复领取返回同份结果。</p>
 <div class="scroll"><table><thead><tr><th>任务</th><th>实验</th><th>状态</th><th>扣费</th><th>时间</th><th>说明</th><th>操作</th></tr></thead>
 <tbody>${releases.map(r=>releaseRow(r,codes[r.experiment_id])).join('')||'<tr><td colspan="7">暂无发布任务。领取后将冻结样本并扣减一次额度。</td></tr>'}</tbody></table></div>
 <h3>当前结果</h3>${detail()}
 <p><button id="privacy-refresh">刷新任务与额度</button></p>
 </section><aside>
 <h2>领取差分汇总</h2>
 ${budgets.length?`<form id="privacy-claim">${select('实验样本','experiment_id',budgets.map(b=>[b.experiment_id,b.code+' · 剩余 '+fmtEps(b.remaining_epsilon)]))}
 <p class="meta">同一冻结样本只扣一次额度；样本新增后为新指纹，消耗新额度。预算不足时服务端拒绝外发。</p>
 <button class="primary">领取并冻结结果</button></form>`:'<p class="notice">尚无实验预算，请负责人先配置。</p>'}
 <h3>配置隐私预算</h3>
 ${state.user.role==='admin'?`<form id="privacy-budget">${select('实验','experiment_id',budgets.map(b=>[b.experiment_id,b.code]))}
 <label>总隐私预算 ε<input name="total_epsilon" type="number" min="0.000001" max="1000" step="0.000001" required value="${budgets[0]?Number(budgets[0].total_epsilon):3}"></label>
 <label>单次领取额度 ε<input name="epsilon_per_release" type="number" min="0.000001" max="1000" step="0.000001" required value="${budgets[0]?Number(budgets[0].epsilon_per_release):1}"></label>
 <p class="meta">总预算不可低于已用额度；单次额度不可高于总预算。修改只影响后续领取。</p>
 <button class="primary">保存预算</button></form>`:'<p class="notice">仅负责人可配置预算；分析员可领取，观察员只读。</p>'}
 ${claimable.length===0?'<p class="meta">当前没有剩余额度足够的实验。</p>':''}
 </aside></div>`;
}
