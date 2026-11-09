import {useEffect,useRef,useState,type FormEvent} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import RFB from '@novnc/novnc';
import {api,type Bot} from './api';
import {Panel} from './Management';

type ComputerState={desktop:{installed:boolean;running:boolean;geometry:string;blocker:string|null;memory_available_mb:number|null;browser:string|null};lease:{holder:'agent'|'human';epoch:number;reason:string};mine:boolean;input_ready:boolean;admission_paused:boolean;private_session:boolean};

export function Computer({open,onClose,bot}:{open:boolean;onClose:()=>void;bot?:Bot}) {
  const query=useQueryClient(),canvas=useRef<HTMLDivElement>(null),rfb=useRef<RFB|null>(null);
  const [expanded,setExpanded]=useState(false),[connection,setConnection]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[secret,setSecret]=useState(''),[notice,setNotice]=useState('');
  const state=useQuery({queryKey:['computer',bot?.id],queryFn:()=>api<ComputerState>(`/api/v1/bots/${bot!.id}/computer`),enabled:open&&!!bot,refetchInterval:open?1500:false,retry:false});
  const thumbnail=useQuery({queryKey:['computer-thumbnail',bot?.id],queryFn:()=>api<{image:string|null}>(`/api/v1/bots/${bot!.id}/computer/thumbnail`),enabled:open&&!!bot&&state.data?.desktop.running&&!expanded,refetchInterval:open?2000:false,retry:false});
  const mine=state.data?.mine&&state.data.input_ready&&state.data.lease.holder==='human';
  // The owner in control sees only their private browser, so connect once it is ready.
  const waiting=!!mine&&!state.data?.private_session;
  useEffect(()=>{if(open){setExpanded(false);setError('');setSecret('');setNotice('');}},[open,bot?.id]);
  useEffect(()=>{if(!open||!expanded||!bot||!state.data?.desktop.running||waiting||!canvas.current)return;setConnection('Connecting…');const protocol=location.protocol==='https:'?'wss:':'ws:';const instance=new RFB(canvas.current,`${protocol}//${location.host}/api/v1/bots/${bot.id}/computer/ws`);instance.scaleViewport=true;instance.resizeSession=false;instance.background='#20251e';instance.viewOnly=!mine;rfb.current=instance;instance.addEventListener('connect',()=>setConnection('Connected'));instance.addEventListener('disconnect',()=>{setConnection('Disconnected · reconnect to continue viewing');setSecret('');});return()=>{instance.disconnect();rfb.current=null;};},[open,expanded,bot?.id,state.data?.desktop.running,waiting,state.data?.private_session]);
  useEffect(()=>{if(rfb.current)rfb.current.viewOnly=!mine;if(!mine)setSecret('');},[mine]);
  useEffect(()=>{if(open&&mine&&state.data?.private_session)setExpanded(true);},[open,mine,state.data?.private_session]);
  async function action(path:string,body:unknown={}){setBusy(true);setError('');setNotice('');try{await api(`/api/v1/bots/${bot!.id}/computer/${path}`,{method:'POST',body:JSON.stringify(body)});await query.invalidateQueries({queryKey:['computer',bot?.id]});await query.invalidateQueries({queryKey:['state']});}catch(err){setError((err as Error).message);}finally{setBusy(false);}}
  function close(){setSecret('');onClose();}
  async function enterSecret(event:FormEvent){event.preventDefault();if(!rfb.current||!mine||connection!=='Connected')return;const destination=rfb.current;const value=secret;setSecret('');setError('');
    destination.focus();for(const char of value){const cp=char.codePointAt(0)!;destination.sendKey(cp<=255?cp:0x01000000|cp);}setNotice('Typed into the focused computer field. Nothing was added to the conversation.');}
  return <Panel open={open} onClose={close} onEscapeKeyDown={event=>{if(canvas.current?.contains(document.activeElement))event.preventDefault();}} title={`${bot?.name||'Teammate'} computer`} description="Watch this teammate’s desktop, or take over in a private browser and hand it back when you finish.">
    <div className={expanded?'computer-expanded':'computer-preview'}>
      {(error||state.error)&&<p role="alert" className="error-text">{error||state.error?.message}</p>}
      {notice&&<p role="status">{notice}</p>}
      <div className="computer-control-status"><strong>{busy?'Applying computer action…':!state.data?'Checking the computer…':state.data.lease.holder==='human'?(mine?(waiting?'Opening your private browser…':'You are in control · private browser'):state.data.input_ready?'A human session holds control':'Waiting for in-flight work to settle'):'Bot is in control'}</strong>{state.data?.admission_paused&&<small>Bot tools and new dispatch remain paused until handback.</small>}</div>
      <div className="button-row computer-actions">
        {!state.data?null:!state.data.desktop.running?<button className="primary" disabled={busy} onClick={()=>action('start')}>Start computer</button>:<>
          <button className="secondary" disabled={busy} onClick={()=>{setExpanded(!expanded);setSecret('');}}>{expanded?'Show preview':'Expand live view'}</button>
          <button className="primary" disabled={busy} onClick={()=>action('control',{action:'takeover'})}>{mine?'Confirm control':state.data.lease.holder==='human'?'Reclaim control':'Take over privately'}</button>
          <button className="secondary" disabled={busy||state.data.lease.holder==='human'} onClick={()=>action('stop')}>Stop computer</button>
        </>}
        {(state.data?.lease.holder==='human'||state.data?.admission_paused)&&<button className="secondary" disabled={busy} onClick={()=>action('control',{action:'handback',force:!mine})}>{mine?'Hand back':'Hand back (force)'}</button>}
      </div>
      {state.data?.desktop.blocker&&<p>{state.data.desktop.blocker}</p>}
      {mine&&state.data?.private_session&&<p className="computer-note">This is a private browser. The bot can’t see this screen or what you type. When you hand back, the bot receives this browser’s logins (cookies), not your passwords. The bot chose these pages, so check the address bar before you enter a password.</p>}
      {expanded?<><div className="computer-canvas" ref={canvas} aria-label="Live bot computer"/><p className="computer-connection" role="status">{connection} · {mine?'Private browser · keyboard and pointer enabled':'View only'}</p>{connection.startsWith('Disconnected')&&<button className="secondary" onClick={()=>{setExpanded(false);setTimeout(()=>setExpanded(true),0);}}>Reconnect live view</button>}</>:thumbnail.data?.image?<button className="computer-thumbnail" aria-label="Expand computer preview" onClick={()=>setExpanded(true)}><img src={thumbnail.data.image} alt={`${bot?.name} live desktop preview`}/></button>:<div className="computer-empty">{state.data?.desktop.running?'Capturing desktop preview…':'The desktop is stopped. Browser state is retained in this teammate’s private volume.'}</div>}
      {expanded&&mine&&<form className="computer-secret" onSubmit={enterSecret}><label>Secure input<input aria-label="Secure computer input" type="password" autoComplete="new-password" value={secret} onChange={e=>setSecret(e.target.value)} maxLength={2000}/></label><p>Click the destination field on the computer first. This types through the live connection and clears the input immediately.</p><button className="secondary" disabled={!secret||connection!=='Connected'}>Type into focused field</button></form>}
      {expanded&&mine&&<div className="button-row"><button className="secondary" disabled={connection!=='Connected'} onClick={()=>rfb.current?.sendKey(0xff0d)}>Press Enter on computer</button><button className="secondary" disabled={connection!=='Connected'} onClick={()=>rfb.current?.sendKey(0xff09)}>Press Tab on computer</button><button className="secondary" disabled={connection!=='Connected'} onClick={()=>rfb.current?.sendKey(0xff1b)}>Press Escape on computer</button></div>}
      <p className="computer-note">Closing this pane or losing the connection keeps human control. Reconnect and hand back explicitly when ready.</p>
    </div>
  </Panel>;
}
