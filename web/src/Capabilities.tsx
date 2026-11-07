import type {Task} from './api';

export type CapabilityView={inventory_known:boolean;running:boolean;human_control:boolean;native_tools:string[];connector_restricted:boolean;blocked_tools:{name:string;message:string}[];connections:{name:string;provider:string;actions:string[]}[]};

const tools=[['Read local files','read_file'],['Edit local files','write_file'],['Run local commands','terminal'],['Use the browser','browser_navigate'],['Use the desktop','computer_use'],['Consult skills','skill_view'],['Maintain memory','memory']];

export function CapabilitySummary({view}:{view?:CapabilityView}){
  if(!view)return null;
  return <details className="capability-summary"><summary>Tools & application access</summary>
    {(!view.inventory_known||!view.running)&&<p role="status">Wait for this teammate’s runtime to refresh before assigning work.</p>}
    {view.human_control&&<p role="status">Hand back the computer when you want this teammate to continue.</p>}
    {tools.map(([label,name])=><div className="capability-row" key={name}><strong>{label}</strong><span>{view.native_tools.includes(name)?'Available':view.blocked_tools.find(b=>b.name===name)?.message||'Not enabled for this teammate'}</span></div>)}
    {!!view.connections.length&&<p>App connections keep enabled tools available. Connector actions use the permissions configured in Connections.</p>}
    {view.connections.map(app=><p key={app.name}><strong>{app.name}</strong> · {app.actions.length} permitted application actions</p>)}
    {!view.connections.length&&<p>No connected applications are assigned to this teammate.</p>}
  </details>;
}

export function TaskPreparation({task}:{task:Task}){
  const plan=task.execution_plan,handoff=task.handoff_context;
  if(!plan?.version&&!handoff?.source_task_id)return null;
  const blockers=[...(plan?.blockers||[]),...(plan?.handoff_blockers||[])];
  return <div className="task-preparation">
    {!!plan?.version&&<><strong>Work preparation</strong><p>Consulted skills: {plan.skills?.map(s=>s.name).join(', ')||'General task guidance'}</p>{blockers.map((b,i)=><p className="error-text" key={i}>{b.message}</p>)}</>}
    {handoff?.source_task_id&&<><strong>Teammate brief</strong>{handoff.expected_output&&<p>{handoff.expected_output}</p>}<small>{handoff.attachment_ids.length} shared attachments · {handoff.artifact_ids.length} shared files</small></>}
  </div>;
}
