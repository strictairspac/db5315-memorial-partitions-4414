/** @typedef {{id:number, title:string, payload:object, author:string}} BusinessCase */
/** @typedef {{id:number, case_id:number, status:'queued'|'running'|'complete'|'failed'}} Run */
/** @typedef {{id:number, case_id:number, version:number, status:'previewed'|'stale'|'applying'|'applied'|'failed', cutoff:string, preview:object, rejection_reason:string|null, report:object|null}} Plan */
/** @typedef {{id:number, run_id:number|null, plan_id:number|null, digest:string, report:object}} Evidence */
