import {Circle, ChevronRight, Layers3, Square} from 'lucide-react';
import {api, mutate, settled, statusLabel, taskStatus, type Artifact, type Bot, type Task, type WorkItem} from './api';
import {OutcomeActions} from './OutcomeActions';

type Props = {
  items:WorkItem[]; botById:(id:string)=>Bot|undefined;
  action:(work:()=>Promise<unknown>)=>Promise<void>; inspect:(task:Task)=>void;
  saveSkill:(task:Task)=>void; edit:(task:Task)=>void; redirect:(task:Task)=>void;
  reviewOutcome:()=>void;
  openOutput:(artifact:Artifact)=>void;
};

export function WorkItems({items,botById,action,inspect,saveSkill,edit,redirect,reviewOutcome,openOutput}:Props) {
  const visible=items.filter(w=>!w.hidden_at&&w.current_task);
  const active=visible.filter(w=>!['completed','failed','cancelled','interrupted'].includes(w.status));
  const finished=visible.filter(w=>!active.includes(w));
  const hidden=items.filter(w=>w.hidden_at&&w.current_task);
  function card(item:WorkItem) {
    const task=item.current_task!;
    const unsettled=item.executions.filter(t=>!t.parent_id&&!settled(t));
    const unknown=item.executions.some(t=>['dispatching','uncertain','unresolved'].includes(t.connector_state||''));
    return <section className="task-card" key={item.id} data-work-item-id={item.id}>
      <div className="task-card-header"><span className={`status-mark ${item.status}`}><Circle size={9} fill="currentColor"/>{item.status===task.status?taskStatus(task):statusLabel(item.status)}</span>{task.grant_id&&<span className="manager-badge">Team task</span>}</div>
      <h3>{item.title}</h3>
      {!item.outcomes_version&&item.status==='completed'&&<small>Historical completion · required results were not recorded</small>}
      <div className="task-owner"><span>{botById(item.bot_id)?.name||item.bot_id} owns this work</span></div>
      {!!item.changes?.length&&<details className="work-revisions"><summary>Request history · revision {item.revision}</summary>{item.changes.map(change=><p key={change.revision}>Revision {change.revision}: {change.change.summary||change.change.objective}<small>Recorded {new Date(change.created_at).toLocaleString()}</small></p>)}</details>}
      {!!item.directions?.some(direction=>!['delivered','included','superseded'].includes(direction.status))&&<p className="form-note">{item.directions?.some(direction=>direction.status==='uncertain')?'Your updated instructions are recorded, but their delivery needs reconciliation. Inspect the work for details.':'Your updated instructions are recorded and awaiting delivery to the affected teammates.'}</p>}
      {!!item.outcomes?.length&&<details className="work-outcomes"><summary>{item.progress?.fulfilled||0} of {item.progress?.required||0} required results ready</summary>{item.outcomes.filter(outcome=>outcome.status!=='cancelled').map(outcome=><div key={outcome.id} className="work-outcome"><strong>{outcome.title}</strong><span>{outcome.status==='blocked'?'Blocked':statusLabel(outcome.status)}{!outcome.required?' · optional':''}</span>{outcome.reason&&<p>{outcome.reason}</p>}{outcome.evidence_strength&&<small>{({reviewed_artifact:'Reviewed artifact',provider_receipt:'Confirmed by provider',artifact_exists:'Published artifact available',model_reported:'Reported by bot',owner_attested:'Confirmed by owner'} as Record<string,string>)[outcome.evidence_strength]||outcome.evidence_strength}</small>}{outcome.contributions?.map(contribution=><small key={contribution.task_id}>{botById(contribution.bot_id)?.name||contribution.bot_id}: {statusLabel(contribution.status)}{contribution.required?' · required':' · optional'}</small>)}{outcome.acceptance?.evidence.review&&<small>Acceptance: {outcome.acceptance.evidence.review}</small>}<OutcomeActions key={`${outcome.id}:${outcome.revision}`} outcome={outcome}/></div>)}</details>}
      <div className="task-actions"><button className="inspect-work" onClick={()=>inspect(task)}>Inspect work</button>{item.status==='completed'&&task.status==='completed'&&<button className="inspect-work" onClick={()=>saveSkill(task)}>Save as skill</button>}<button className="inspect-work remove-work" onClick={()=>action(()=>api(`/api/v1/work-items/${item.id}/hide`,{method:'POST'}))}>Remove from work list</button></div>
      <div className="assignments">{item.executions.filter(t=>t.parent_id).map(child=><div className="assignment" key={child.id} style={{paddingLeft:Math.max(0,child.depth-1)*10}}><ChevronRight size={13}/><span><strong>{botById(child.bot_id)?.name}</strong><small>{taskStatus(child)}</small></span><i className={`presence ${settled(child)?'':'working'}`}/></div>)}</div>
      {item.executions.filter(t=>!t.parent_id).length>1&&<small className="work-revisions">{item.executions.filter(t=>!t.parent_id).length} assignments and revisions in this work item</small>}
      {!!item.outputs?.length&&<div className="work-outputs" aria-label="Work item outputs">{item.outputs.slice(0,3).map(output=><button className="inspect-work" key={output.id} onClick={()=>openOutput(output)}>{output.name}</button>)}</div>}
      {task.error&&(item.status==='completed'?<details className="work-execution-note"><summary>Execution note</summary><p className="form-note">All required results are ready. The latest execution also reported:</p><p className="task-error">{task.error}</p></details>:<p className="task-error">{task.error}</p>)}
      {unknown&&<><p className="task-error">Check the previous application outcome before continuing this work.</p><button className="secondary" onClick={reviewOutcome}>Review application outcome</button></>}
      {item.status!=='completed'&&['failed','interrupted','cancelled'].includes(task.status)&&<button className="secondary" disabled={unknown} onClick={()=>action(()=>mutate(`/api/v1/tasks/${task.id}/retry`,'POST',{}))}>Retry task</button>}
      {task.status==='paused_budget'&&task.stop_kind==='budget'&&item.status!=='completed'&&<><p className="form-note">The active work budget was reached. Completed results are preserved.</p><button className="secondary" disabled={unknown} onClick={()=>action(()=>mutate(`/api/v1/tasks/${task.id}/continue`,'POST',{}))}>Continue</button></>}
      {unsettled.map(run=><div key={run.id} className="task-controls">{run.attempts===0&&['queued','blocked'].includes(run.status)&&<button onClick={()=>edit(run)}>Edit instructions</button>}{!['paused_budget','pausing_budget'].includes(run.status)&&<button onClick={()=>redirect(run)}>Redirect</button>}<button onClick={()=>action(()=>api(`/api/v1/tasks/${run.id}/stop`,{method:'POST'}))}><Square size={11}/>Stop {run.grant_id?'team task':'task'}</button>{run.grant_id&&<button onClick={()=>action(()=>api(`/api/v1/grants/${run.grant_id}/revoke`,{method:'POST'}))}>Revoke management authority</button>}</div>)}
    </section>;
  }
  return <>
    {!visible.length&&<div className="context-empty"><Layers3 size={26}/><h3>A clear view of the work.</h3><p>Actual assignments appear here. Discussion and progress questions stay in the conversation.</p></div>}
    {active.map(card)}
    {!!finished.length&&<details className="work-history"><summary>Finished work ({finished.length})</summary>{finished.map(card)}</details>}
    {!!hidden.length&&<details className="work-history"><summary>Removed work ({hidden.length})</summary>{hidden.map(w=><div className="removed-work" key={w.id}><div className="removed-work-main"><strong>{w.title}</strong><small>{statusLabel(w.status)} · {botById(w.bot_id)?.name||w.bot_id}</small></div><div className="removed-work-actions"><button onClick={()=>action(()=>api(`/api/v1/work-items/${w.id}/restore`,{method:'POST'}))}>Restore to work list</button>{!['completed','failed','cancelled','interrupted'].includes(w.status)&&<button onClick={()=>inspect(w.current_task!)}>Inspect active work</button>}</div></div>)}</details>}
  </>;
}
