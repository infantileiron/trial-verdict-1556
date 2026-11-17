/** @typedef {{id:number,code:string,name:string,starts_at:string,ends_at:string,a_ratio:number,hypothesis:string}} Experiment */
/** @typedef {{id:number,experiment_id:number,user_key:string,group:'A'|'B',kind:'exposure'|'activation',occurred_at:string}} TrialEvent */
/** @typedef {{id:number,experiment_id:number,state:string,result:object,snapshot:object,rationale:string,action:string,published_at:string}} Decision */
export const CONTRACT_VERSION='1.0';
/** @typedef {{experiment_id:number,code:string,name:string,total_epsilon:number,used_epsilon:number,remaining_epsilon:number,epsilon_per_release:number,releases_charged:number}} PrivacyBudget */
/** @typedef {{id:number,experiment_id:number,sample_ref:string,status:'queued'|'running'|'done'|'failed',epsilon:number,result:object|null,error:string,created_at:string,completed_at:string}} DpRelease */
/**
 * 外发契约（仅渠道汇总）：schema/version/experiment_code/sample_ref、加噪 counts（exposed_a/exposed_b/activated_a/activated_b）、
 * noise（机制、ε、敏感度、误差半宽）、availability（usable/reasons/notes/hint）、notice、release_scope。
 * 不得包含 user_key、原始事件、requested_by 或内部裁决字段。
 */
export const PRIVACY_CONTRACT_VERSION='laplace-counts-1.0';
