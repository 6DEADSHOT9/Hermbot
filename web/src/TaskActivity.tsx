import {type Task} from './api';

function elapsed(value:string) {
  const seconds=Math.max(0,Math.floor((Date.now()-Date.parse(value))/1000));
  if(!Number.isFinite(seconds))return 'time unavailable';
  if(seconds<60)return `${seconds} sec ago`;
  if(seconds<3600)return `${Math.floor(seconds/60)} min ago`;
  return `${Math.floor(seconds/3600)} hr ago`;
}

export function TaskActivity({task}:{task:Task}) {
  const activity=task.activity;
  if(!activity)return null;
  const state:Record<string,string>={'tool.admitted':'admitted; waiting to start','tool.started':'started','tool.completed':'completed'};
  return <div className="execution-activity" aria-label="Execution activity">
    {task.status==='running'&&!!activity.waiting_model_requests&&<small role="status">Waiting for shared model capacity</small>}
    {activity.last_tool&&<small>Last tool activity: {activity.last_tool} · {state[activity.last_tool_event||'']||'observed'}{activity.last_tool_at?` · ${elapsed(activity.last_tool_at)}`:''}</small>}
    <small>{activity.last_progress_at?`Last progress: ${elapsed(activity.last_progress_at)}`:activity.started_at?'No progress event observed yet':'Execution has not started'}</small>
    {activity.last_contact_at&&<small>Last runtime contact: {elapsed(activity.last_contact_at)}</small>}
  </div>;
}
