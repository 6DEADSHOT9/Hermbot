import {useEffect,useState,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api,mutate,type Approval,type InputRequest,type Artifact,type Conversation,type Message,type Task,type WorkItem} from './api';
import Markdown from 'react-markdown';
import {Panel} from './Management';
import {InputCard} from './InputCard';
import {statusLabel} from './api';
import './Attention.css';

type Item={id:string;version:number;title:string;summary:string;source_status:string;active:boolean;needs_owner:boolean;unread:boolean;updated_at:string;refs:{bot_id?:string;bot_name?:string;settings?:string;source_id?:string;source_type?:string;message_id?:string}};
export type AttentionTarget=Item&{work:WorkItem|null;task:Task|null;conversation:Conversation|null;thread:Message|null;request?:InputRequest|Approval|null};
export type Inbox={items:Item[];unread:number;needs_owner:number;next:string|null};
export function Attention({open,onClose,initial,onNavigate,onSettings,renderReview,onArtifact}:{open:boolean;onClose:()=>void;initial:string|null;onNavigate:(value:AttentionTarget)=>Promise<void>;onSettings:(section:string,identity?:string,kind?:string)=>void;renderReview:(review:Approval,onError:(message:string)=>void)=>ReactNode;onArtifact:(artifact:Artifact)=>void}) {
  const query=useQueryClient();
  const [view,setView]=useState('needs'),[before,setBefore]=useState<string|null>(null),[selected,setSelected]=useState<string|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const inbox=useQuery({queryKey:['attention',view,before],queryFn:()=>api<Inbox>(`/api/v1/attention?view=${view}${before?`&before=${before}`:''}`),enabled:open,refetchInterval:5000});
  const detail=useQuery({queryKey:['attention-detail',selected],queryFn:()=>api<AttentionTarget>(`/api/v1/attention/${selected}`),enabled:open&&!!selected,refetchInterval:5000});
  useEffect(()=>{if(open){setSelected(initial&&initial!=='inbox'?initial:null);setError('');}},[open,initial]);
  async function change(items:Item[],action:string) {
    setError('');setBusy(true);
    try {await api('/api/v1/attention/read',{method:'POST',body:JSON.stringify({items:items.map(({id,version})=>({id,version})),action})});await query.invalidateQueries({queryKey:['attention']});await query.invalidateQueries({queryKey:['attention-counts']});}
    catch(err){setError((err as Error).message);}finally{setBusy(false);}
  }
  // A teammate message given up after repeated failures: resend it, or mark it done. Either one closes the item.
  async function resolveMessage(target:AttentionTarget,action:'resend'|'done') {
    setError('');setBusy(true);
    try {await mutate(`/api/v1/teammate-messages/${target.refs.message_id}/resolve`,'POST',{action});await change([target],'read');await query.invalidateQueries({queryKey:['attention-detail']});}
    catch(err){setError((err as Error).message);}finally{setBusy(false);}
  }
  const value=detail.data;
  return <Panel open={open} onClose={onClose} title="Attention" description="Questions, reviews and results from your teammates.">
    <div className="attention-toolbar"><div className="attention-tabs" role="group" aria-label="Attention view">{[['needs','Needs you'],['updates','Updates'],['history','History']].map(([key,label])=><button key={key} aria-pressed={view===key} onClick={()=>{setView(key);setBefore(null);setSelected(null);}}>{label}</button>)}</div><button className="secondary" disabled={busy||!!selected||!inbox.data?.items.length} onClick={()=>void change(inbox.data!.items,'read')}>Mark visible read</button></div>
    <p className="attention-counts">{inbox.data?.needs_owner??'…'} need your response · {inbox.data?.unread??'…'} unread updates</p>
    {(error||inbox.error||detail.error)&&<p role="alert" className="task-error">{error||inbox.error?.message||detail.error?.message} <button className="text-button" onClick={()=>{void inbox.refetch();void detail.refetch();}}>Retry</button></p>}
    {selected?<section className="attention-detail"><button className="text-button" onClick={()=>setSelected(null)}>← Back to list</button>{detail.isPending?<p>Loading current status…</p>:value&&<>
      <h3>{value.work?.title||value.title||'Saved update'}</h3>{!value.request&&<p>{value.summary}</p>}<p className="form-note">{value.refs.bot_name&&`${value.refs.bot_name} · `}{value.active?statusLabel(value.source_status):'This request is resolved or no longer actionable.'}</p>
      {value.active&&value.request&&('question_hash' in value.request?<InputCard key={value.request.id} request={value.request}/>:renderReview(value.request as Approval,setError))}
      {value.work&&<p>{value.work.title} · {value.work.progress?.fulfilled??0} of {value.work.progress?.required??0} required results ready</p>}
      {!value.request&&value.task?.output&&<div className="prose attention-report"><Markdown>{value.task.output}</Markdown></div>}
      {!!value.work?.outputs.length&&<div className="attention-outputs">{value.work.outputs.map(artifact=><button key={artifact.id} className="secondary" onClick={()=>onArtifact(artifact)}>{artifact.name}</button>)}</div>}
      {value.active&&value.refs.message_id&&<div className="button-row">{value.needs_owner&&<button className="primary" disabled={busy} onClick={()=>void resolveMessage(value,'resend')}>Resend message</button>}<button className="secondary" disabled={busy} onClick={()=>void resolveMessage(value,'done')}>Done</button></div>}
      <div className="button-row"><button className="primary" disabled={busy} onClick={async()=>{setBusy(true);try{if(value.refs.settings)onSettings(value.refs.settings,value.refs.source_id,value.refs.source_type);else await onNavigate(value);await change([value],'read');onClose();}catch(err){setError((err as Error).message);}finally{setBusy(false);}}}>{value.refs.settings?'Open settings':'Open work'}</button><button className="secondary" disabled={busy} onClick={()=>void change([value],'read')}>Mark read</button></div>
    </>}</section>:<div className="attention-list">
      {inbox.isPending?<p className="form-note">Loading attention…</p>:!inbox.data?.items.length?<p className="attention-empty">{view==='needs'?'Nothing needs your response.':view==='updates'?'You’re up to date.':'No saved updates in this view.'}</p>:inbox.data.items.map(item=><article className="attention-row" key={item.id}>
        <button className="attention-open" onClick={()=>setSelected(item.id)}><span className="attention-row-title">{item.unread&&<span className="unread-dot" aria-label="Unread"/>}<strong>{item.title||'Saved update'}</strong></span><span>{item.summary}</span><small>{item.refs.bot_name&&`${item.refs.bot_name} · `}{item.active?statusLabel(item.source_status):'Resolved'} · {new Date(item.updated_at).toLocaleString()}</small></button>
        <div className="attention-row-actions"><button onClick={()=>void change([item],item.unread?'read':'unread')} disabled={busy}>{item.unread?'Mark read':'Mark unread'}</button>{!(item.active&&item.needs_owner)&&<button onClick={()=>void change([item],'dismiss')} disabled={busy}>Dismiss</button>}</div>
      </article>)}
      {(before||inbox.data?.next)&&<div className="button-row">{before&&<button className="secondary" onClick={()=>setBefore(null)}>Newest</button>}{inbox.data?.next&&<button className="secondary" onClick={()=>setBefore(inbox.data!.next)}>Older updates</button>}</div>}
    </div>}
  </Panel>;
}
