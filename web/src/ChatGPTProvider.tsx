import {useEffect,useRef,useState} from 'react';
import {useQueryClient} from '@tanstack/react-query';
import {api,mutate,type Provider} from './api';

export function AddChatGPTProvider({online}:{online?:boolean}){
  const query=useQueryClient();
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  return <div className="settings-form"><h3>Use your Codex subscription</h3><p className="form-note">Connect an eligible account on this Mac. Teammates and application processing share its allowance.</p>
    {online===false&&<p role="status" className="form-note">The local account service is unavailable. Check the Hermbot broker and unlock macOS Keychain before connecting.</p>}
    <button className="secondary" disabled={busy} onClick={async()=>{setBusy(true);setError('');try{await api('/api/v1/providers/codex',{method:'POST'});await query.invalidateQueries({queryKey:['settings']});}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>{busy?'Adding connection…':'Connect Codex subscription'}</button>
    {error&&<p role="alert" className="error-text">{error}</p>}</div>;
}

export function ChatGPTProvider({provider}:{provider:Provider}){
  const query=useQueryClient();
  const generation=useRef(0);
  const codex=provider.auth_kind==='codex_oauth';
  const [connection,setConnection]=useState<{error?:string;device_code?:string;verification_url?:string}>({});
  const [busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [model,setModel]=useState(provider.default_model);
  const [name,setName]=useState(provider.name),[allowed,setAllowed]=useState(provider.models.join(', '));
  const previousName=useRef(provider.name);
  useEffect(()=>{const previous=previousName.current;setName(current=>current===previous?provider.name:current);previousName.current=provider.name;},[provider.name]);
  async function run(action:string){
    const mine=++generation.current;
    setBusy(action);setError('');setNotice('');
    try{
      const result=await api<{revocation_confirmed?:boolean;status?:string;error?:string;device_code?:string;verification_url?:string}>(`/api/v1/providers/${provider.id}/chatgpt/${action}`,{method:'POST',body:JSON.stringify({model:model||provider.default_model})});
      if(mine!==generation.current)return;
      setConnection(result);
      if(action==='signin'&&!codex)await api(`/api/v1/providers/${provider.id}/chatgpt/models`,{method:'POST'});
      if(mine===generation.current)setNotice(action==='cancel'?'Sign-in cancelled.':action==='test'?'Model verified. You can assign it to a teammate.':action==='disconnect'?(result.revocation_confirmed?'Disconnected.':'Disconnected locally. Remote revocation was not confirmed. Review connected sessions in your OpenAI account settings.'):action==='signin'||action==='device'?(codex?'Finish signing in on OpenAI’s website. You can close this panel and return.':'Account connected. Test a model before assigning it.'):'Connection status updated.');
    }catch(e){if(mine===generation.current)setError((e as Error).message);}
    finally{if(mine===generation.current)setBusy('');await query.invalidateQueries({queryKey:['settings']});}
  }
  useEffect(()=>{if(!codex||provider.connection_status!=='connecting')return;let active=true,inFlight=false;const poll=async()=>{if(inFlight)return;inFlight=true;const mine=generation.current;try{const value=await api<{status:string;error?:string;device_code?:string;verification_url?:string}>(`/api/v1/providers/${provider.id}/chatgpt/status`,{method:'POST'});if(!active||mine!==generation.current)return;setConnection(value);if(value.status==='connected')await api(`/api/v1/providers/${provider.id}/chatgpt/models`,{method:'POST'});await query.invalidateQueries({queryKey:['settings']});}catch(e){if(active&&mine===generation.current)setError((e as Error).message);}finally{inFlight=false;}};const timer=window.setInterval(poll,2000);void poll();return()=>{active=false;window.clearInterval(timer);};},[codex,provider.id,provider.connection_status,query]);
  const connecting=provider.connection_status==='connecting'||busy==='signin';
  const connected=provider.connection_status==='connected'&&provider.plan_enabled;
  return <div className="settings-form chatgpt-provider" aria-label={`${provider.name} connection`}>
    <div><h3>{provider.name}</h3><p className="form-note">{provider.account_label||'No account connected'}{provider.account_plan?` · ${provider.account_plan}`:''} · {connecting?'Waiting for OpenAI authorization':(provider.connection_status||'disconnected').replaceAll('_',' ')}</p></div>
    <p className="form-note">{connected?(codex?'Codex account connected. All assigned teammates share its allowance.':'ChatGPT plan usage enabled.'):'Sign in with OpenAI using the local account service.'} Your account tokens stay on this Mac.</p>
    {provider.connection_status==='quota_blocked'&&<p role="status" className="form-note">Usage limit reached. Check ChatGPT usage settings, then recheck availability when ready. <button className="text-button" disabled={!!busy} onClick={()=>run('retry')}>Recheck availability</button></p>}
    <div className="button-row">
      <button className={connected?'secondary':'primary'} disabled={!!busy||connecting} onClick={()=>run('signin')}>{connected?'Reconnect account':codex?'Sign in with Codex':'Continue with ChatGPT'}</button>
      {connecting&&<button className="secondary" onClick={()=>run('cancel')}>Cancel sign-in</button>}
      {codex&&!connecting&&<button className="text-button" disabled={!!busy} onClick={()=>run('device')}>Use a device code</button>}
      {!busy&&<button className="text-button" onClick={()=>run('status')}>Check connection</button>}
      <a className="text-button" href={codex?'https://chatgpt.com/codex/settings/usage':'https://chatgpt.com/settings/usage'} target="_blank" rel="noreferrer">Manage usage</a>
    </div>
    {connected&&<><label>Model<select value={model||provider.default_model} onChange={e=>setModel(e.target.value)}>{provider.models.map(id=><option key={id}>{id}</option>)}</select></label><div className="button-row"><button className="secondary" disabled={!!busy||!provider.models.length} onClick={()=>run('test')}>{busy==='test'?'Testing model…':'Test model'}</button><button className="text-button" disabled={!!busy} onClick={()=>run('models')}>Refresh models</button><button className="text-button" disabled={!!busy} onClick={()=>run('retry')}>Recheck usage availability</button></div><p className="form-note">Verified models: {provider.verified_models?.join(', ')||'None yet'}. API billing is never used as an automatic fallback.</p>
      <details><summary>Connection settings</summary><form className="settings-form" onSubmit={async event=>{event.preventDefault();setBusy('save');setError('');try{await mutate(`/api/v1/providers/${provider.id}`,'PUT',{auth_kind:provider.auth_kind,name,base_url:provider.base_url,models:allowed.split(',').map(v=>v.trim()).filter(Boolean),default_model:model||provider.default_model,expected_revision:provider.revision});setNotice('Connection settings saved.');await query.invalidateQueries({queryKey:['settings']});}catch(e){setError((e as Error).message);}finally{setBusy('');}}}><label>Connection name<input required maxLength={80} value={name} onChange={e=>setName(e.target.value)}/></label><label>Permitted model IDs<input required value={allowed||provider.models.join(', ')} onChange={e=>setAllowed(e.target.value)}/></label><p className="form-note">Available models: {provider.discovered_models?.join(', ')}. The model selected above becomes the default when you save.</p><button className="secondary" disabled={!!busy}>Save connection settings</button></form></details></>}
    {!busy&&<div className="button-row"><button className="danger-text" onClick={()=>run('disconnect')}>Disconnect account</button>{provider.connection_status==='disconnected'&&<button className="text-button" onClick={async()=>{setBusy('remove');try{await api(`/api/v1/providers/${provider.id}`,{method:'DELETE'});await query.invalidateQueries({queryKey:['settings']});}catch(e){setError((e as Error).message);}finally{setBusy('');}}}>Remove connection</button>}</div>}
    {connection.device_code&&connecting&&<p role="status" className="form-note">Open <a href="https://auth.openai.com/codex/device" target="_blank" rel="noreferrer">OpenAI device sign-in</a> and enter <strong>{connection.device_code}</strong>. Treat this code as private.</p>}
    {connection.error&&<p role="alert" className="error-text">{connection.error.replaceAll('_',' ')}. Try signing in again. If the callback port is busy, use a device code.</p>}
    {notice&&<p role="status" className="form-note">{notice}</p>}{error&&<p role="alert" className="error-text">{error}</p>}
  </div>;
}
