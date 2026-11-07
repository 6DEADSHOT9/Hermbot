import {useEffect,useRef,useState,type FormEvent} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import RFB from '@novnc/novnc';
import {api,formatFileSize,type Bot} from './api';
import {Panel} from './Management';
import {Teaching,activeCapture} from './Teaching';

type CaptureStatus={id:string;state:string;last_seq:number;retained_bytes:number;error:string|null};

export function TeachingCaptureStatus({bot,onOpen,hidden}:{bot:Bot;onOpen:()=>void;hidden:boolean}){
  const [,refresh]=useState(0);
  const captures=useQuery({queryKey:['teaching',bot.id],queryFn:()=>api<CaptureStatus[]>(`/api/v1/bots/${bot.id}/teaching`),refetchInterval:3000,retry:false});
  let closed='';try{closed=sessionStorage.getItem(`teaching-closed:${bot.id}`)||'';}catch{/* Status does not require storage. */}
  const row=captures.data?.find(c=>activeCapture(c.state))||captures.data?.find(c=>c.id===closed);
  if(hidden||(!row&&!closed))return null;
  return <aside className="teaching-capture-strip" aria-label={`${bot.name} recording status`}><div role="status"><strong>{bot.name} · {captures.error?'Recording status unconfirmed':!row?'Recording unavailable':row.state==='recording'?'Recording':row?.state==='paused'?'Recording paused':row?.state==='finalizing'?'Saving recording':row?.state==='preparing'?'Ready to record':'Recording retained'}</strong><span>{row?`${row.last_seq} observations saved so far · ${formatFileSize(row.retained_bytes)}`:'Reconnect to check saved progress.'}</span>{row?.error&&<span>{row.error}</span>}</div><button className="secondary" onClick={onOpen}>{row?.state==='paused'?'Resume or finish':'Open recording'}</button>{row&&!activeCapture(row.state)&&<button className="secondary" onClick={()=>{try{sessionStorage.removeItem(`teaching-closed:${bot.id}`);}catch{/* Dismiss is optional. */}refresh(n=>n+1);}}>Dismiss status</button>}</aside>;
}

type ComputerState={desktop:{installed:boolean;running:boolean;geometry:string;blocker:string|null;memory_available_mb:number|null;browser:string|null};lease:{holder:'agent'|'human';epoch:number;reason:string};mine:boolean;input_ready:boolean;admission_paused:boolean};

export function Computer({open,onClose,bot}:{open:boolean;onClose:()=>void;bot?:Bot}) {
  const query=useQueryClient(),canvas=useRef<HTMLDivElement>(null),rfb=useRef<RFB|null>(null);
  const [expanded,setExpanded]=useState(false),[connection,setConnection]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[secret,setSecret]=useState(''),[notice,setNotice]=useState('');
  const closing=useRef(false);
  const [unsaved,setUnsaved]=useState(false),[discardOffered,setDiscardOffered]=useState(false);
  const state=useQuery({queryKey:['computer',bot?.id],queryFn:()=>api<ComputerState>(`/api/v1/bots/${bot!.id}/computer`),enabled:open&&!!bot,refetchInterval:open?1500:false,retry:false});
  const thumbnail=useQuery({queryKey:['computer-thumbnail',bot?.id],queryFn:()=>api<{image:string|null}>(`/api/v1/bots/${bot!.id}/computer/thumbnail`),enabled:open&&!!bot&&state.data?.desktop.running&&!expanded,refetchInterval:open?2000:false,retry:false});
  const mine=state.data?.mine&&state.data.input_ready&&state.data.lease.holder==='human';
  useEffect(()=>{if(!open||!bot)return;const id=bot.id;return()=>{void fetch(`/api/v1/bots/${id}/teaching-dismiss`,{method:'POST',credentials:'same-origin',keepalive:true}).then(response=>{if(!response.ok)void query.invalidateQueries({queryKey:['teaching',id]});}).catch(()=>{void query.invalidateQueries({queryKey:['teaching',id]});});};},[open,bot?.id,query]);
  useEffect(()=>{if(open){setExpanded(false);setError('');setSecret('');setNotice('');}},[open,bot?.id]);
  useEffect(()=>{if(!open||!expanded||!bot||!state.data?.desktop.running||!canvas.current)return;setConnection('Connecting…');const protocol=location.protocol==='https:'?'wss:':'ws:';const instance=new RFB(canvas.current,`${protocol}//${location.host}/api/v1/bots/${bot.id}/computer/ws`);instance.scaleViewport=true;instance.resizeSession=false;instance.background='#20251e';instance.viewOnly=!mine;rfb.current=instance;instance.addEventListener('connect',()=>setConnection('Connected'));instance.addEventListener('disconnect',()=>{setConnection('Disconnected · reconnect to continue viewing');setSecret('');});return()=>{instance.disconnect();rfb.current=null;};},[open,expanded,bot?.id,state.data?.desktop.running]);
  useEffect(()=>{if(rfb.current)rfb.current.viewOnly=!mine;if(!mine)setSecret('');},[mine]);
  async function action(path:string,body:unknown={}){setBusy(true);setError('');setNotice('');try{await api(`/api/v1/bots/${bot!.id}/computer/${path}`,{method:'POST',body:JSON.stringify(body)});await query.invalidateQueries({queryKey:['computer',bot?.id]});await query.invalidateQueries({queryKey:['state']});}catch(err){setError((err as Error).message);}finally{setBusy(false);}}
  async function close(discard=false){
    if(unsaved&&!discard){setDiscardOffered(true);setError('Save your teaching edits before closing, or explicitly close without saving them.');return;}
    if(closing.current)return;
    if(!bot){onClose();return;}
    closing.current=true;setBusy(true);setSecret('');setError('');
    try{
      const result=await api<{capture:CaptureStatus|null}>(`/api/v1/bots/${bot.id}/teaching-dismiss`,{method:'POST'});
      if(result.capture){try{sessionStorage.setItem(`teaching-closed:${bot.id}`,result.capture.id);sessionStorage.setItem(`teaching-selected:${bot.id}`,result.capture.id);}catch{/* Server capture remains recoverable. */}}
      await query.invalidateQueries({queryKey:['teaching',bot.id]});setDiscardOffered(false);onClose();
    }catch(err){setError(`Could not confirm recording pause: ${(err as Error).message}. This pane remains open; saved progress is shown in teaching.`);}
    finally{closing.current=false;setBusy(false);}
  }
  async function enterSecret(event:FormEvent){event.preventDefault();if(!rfb.current||!mine||connection!=='Connected')return;const destination=rfb.current;const value=secret;setSecret('');setError('');
    try{await api(`/api/v1/bots/${bot!.id}/teaching-private-input`,{method:'POST'});}catch(err){setError(`Private input was not sent: ${(err as Error).message}`);return;}
    if(rfb.current!==destination||!mine)return;
    destination.focus();for(const char of value){const cp=char.codePointAt(0)!;destination.sendKey(cp<=255?cp:0x01000000|cp);}setNotice('Typed into the focused computer field. Nothing was added to the conversation.');}
  return <Panel open={open} onClose={()=>void close()} onEscapeKeyDown={event=>{if(canvas.current?.contains(document.activeElement))event.preventDefault();}} title={`${bot?.name||'Teammate'} computer`} description="View this teammate’s private desktop, take control, and explicitly hand it back when you finish.">
    <div className={expanded?'computer-expanded':'computer-preview'}>
      {(error||state.error)&&<p role="alert" className="error-text">{error||state.error?.message}</p>}
      {discardOffered&&<button className="secondary" onClick={()=>void close(true)}>Close without saving teaching edits</button>}
      {notice&&<p role="status">{notice}</p>}
      <div className="computer-control-status"><strong>{busy?'Applying computer action…':state.data?.lease.holder==='human'?(mine?'You are in control':state.data.input_ready?'A human session holds control':'Waiting for in-flight work to settle'):'Bot is in control'}</strong>{state.data?.admission_paused&&<small>Bot tools and new dispatch remain paused until handback.</small>}</div>
      <div className="button-row computer-actions">
        {!state.data?.desktop.running?<button className="primary" disabled={busy||!state.data} onClick={()=>action('start')}>Start computer</button>:<>
          <button className="secondary" disabled={busy} onClick={()=>{setExpanded(!expanded);setSecret('');}}>{expanded?'Show preview':'Expand live view'}</button>
          <button className="primary" disabled={busy} onClick={()=>action('control',{action:'takeover'})}>{mine?'Confirm control':state.data.lease.holder==='human'?'Reclaim control':'Take over'}</button>
          <button className="secondary" disabled={busy||!mine} onClick={()=>action('browser')}>Open browser</button>
          <button className="secondary" disabled={busy||state.data.lease.holder==='human'} onClick={()=>action('stop')}>Stop computer</button>
        </>}
        {(state.data?.lease.holder==='human'||state.data?.admission_paused)&&<button className="secondary" disabled={busy} onClick={()=>action('control',{action:'handback',force:!mine})}>{mine?'Hand back':'Hand back (force)'}</button>}
      </div>
      {state.data?.desktop.blocker&&<p>{state.data.desktop.blocker}</p>}
      {bot&&<Teaching key={bot.id} botId={bot.id} mine={!!mine} onDirty={setUnsaved}/>}
      {expanded?<><div className="computer-canvas" ref={canvas} aria-label="Live bot computer"/><p className="computer-connection" role="status">{connection} · {mine?'Keyboard and pointer enabled':'View only'}</p>{connection.startsWith('Disconnected')&&<button className="secondary" onClick={()=>{setExpanded(false);setTimeout(()=>setExpanded(true),0);}}>Reconnect live view</button>}</>:thumbnail.data?.image?<button className="computer-thumbnail" aria-label="Expand computer preview" onClick={()=>setExpanded(true)}><img src={thumbnail.data.image} alt={`${bot?.name} live desktop preview`}/></button>:<div className="computer-empty">{state.data?.desktop.running?'Capturing desktop preview…':'The desktop is stopped. Browser state is retained in this teammate’s private volume.'}</div>}
      {expanded&&mine&&<form className="computer-secret" onSubmit={enterSecret}><label>Secure input<input aria-label="Secure computer input" type="password" autoComplete="new-password" value={secret} onChange={e=>setSecret(e.target.value)} maxLength={2000}/></label><p>Click the destination field on the computer first. This types through the live connection and clears the input immediately.</p><button className="secondary" disabled={!secret||connection!=='Connected'}>Type into focused field</button></form>}
      {expanded&&mine&&<div className="button-row"><button className="secondary" disabled={connection!=='Connected'} onClick={()=>rfb.current?.sendKey(0xff0d)}>Press Enter on computer</button><button className="secondary" disabled={connection!=='Connected'} onClick={()=>rfb.current?.sendKey(0xff09)}>Press Tab on computer</button><button className="secondary" disabled={connection!=='Connected'} onClick={()=>rfb.current?.sendKey(0xff1b)}>Press Escape on computer</button></div>}
      <p className="computer-note">Closing this pane or losing the connection keeps human control. Reconnect and hand back explicitly when ready.</p>
    </div>
  </Panel>;
}
