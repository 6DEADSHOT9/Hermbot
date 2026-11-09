import {useEffect,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api} from './api';

type Device={id:string;label:string;enabled:boolean;status:string;current:boolean;expires_at:string};
type Settings={revision:number;push_enabled:boolean;categories:string[];timezone:string;quiet_enabled:boolean;quiet_start:string;quiet_end:string;worker_error:string|null;worker_healthy:boolean;push:{configured:boolean;public_key:string;key_id:string};devices:Device[]};
export async function clearBrowserPush() {
  if(!('serviceWorker' in navigator))return;
  try {const registration=await navigator.serviceWorker.getRegistration('/');if(registration){const sub=await registration.pushManager.getSubscription();await sub?.unsubscribe();const shown=await registration.getNotifications();shown.forEach(item=>item.close());}}catch{/* Server revocation already prevents further dispatch. */}
}
// The Notifications section of Settings.
export function NotificationSettings({active}:{active:boolean}) {
  const query=useQueryClient();
  const settings=useQuery({queryKey:['notification-settings'],queryFn:()=>api<Settings>('/api/v1/notifications/settings'),enabled:active,refetchInterval:10000});
  const [form,setForm]=useState<Settings|null>(null),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{if(active){setForm(null);setError('');setMessage('');}},[active]);
  const value=form||settings.data;
  function field<K extends keyof Settings>(key:K,next:Settings[K]){if(value)setForm({...value,[key]:next});}
  async function run(work:()=>Promise<void>){setBusy(true);setError('');setMessage('');try{await work();await query.invalidateQueries({queryKey:['notification-settings']});}catch(err){setError((err as Error).message);}finally{setBusy(false);}}
  async function save(next:Settings) {
    await api('/api/v1/notifications/settings',{method:'PUT',body:JSON.stringify(Object.fromEntries(['revision','push_enabled','categories','timezone','quiet_enabled','quiet_start','quiet_end'].map(k=>[k,next[k as keyof Settings]])))});
    setForm(null);
  }
  async function enable() {
    if(!value)return;
    if(!window.isSecureContext||!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window))throw new Error('This browser needs a secure origin and Web Push support. Use localhost on this computer, or HTTPS. On iPhone/iPad, open the installed Home Screen app.');
    if(!value.push.configured)throw new Error('The workspace notification keys need setup before enabling this browser.');
    if(Notification.permission==='denied')throw new Error('Notifications are blocked. Allow them in this site’s browser settings, then try again.');
    // Request directly from the explicit button gesture, before any asynchronous network setup.
    const permission=Notification.permission==='granted'?'granted':await Notification.requestPermission();
    if(permission!=='granted')throw new Error('Notification permission was not granted. You can enable it later.');
    const registration=await navigator.serviceWorker.register('/sw.js',{scope:'/'});
    await navigator.serviceWorker.ready;
    let subscription=await registration.pushManager.getSubscription();
    const bytes=Uint8Array.from(atob(value.push.public_key.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-value.push.public_key.length%4)%4)),c=>c.charCodeAt(0));
    if(subscription){const old=subscription.options.applicationServerKey;if(old&&Array.from(new Uint8Array(old)).join()!==Array.from(bytes).join()){await subscription.unsubscribe();subscription=null;}}
    subscription=subscription||await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:bytes});
    const serialized=subscription.toJSON();
    await api('/api/v1/notifications/subscriptions',{method:'POST',body:JSON.stringify({endpoint:serialized.endpoint,keys:serialized.keys,key_id:value.push.key_id,label:'This browser'})});
    if(!value.push_enabled)await save({...value,push_enabled:true});
    setMessage('This browser is enabled. Send a test to check browser and operating-system delivery.');
  }
  return <>
    {(error||settings.error)&&<p className="task-error" role="alert">{error||settings.error?.message}</p>}{message&&<p role="status" className="form-note">{message}</p>}
    {!value?<p>Loading notification preferences…</p>:<>
      {!value.worker_healthy&&<p className="form-note" role="status">The notification worker has not checked in recently. Saved attention will catch up when it returns.</p>}{value.worker_error&&<p className="form-note">{value.worker_error}</p>}
      <section className="notification-browser"><h3>Browser notifications</h3><p>Notification text is private: it only says Hermbot has an update. Open the app to read the details.</p><button className="primary" disabled={busy||!value.push.configured} onClick={()=>void run(enable)}>Enable this browser</button>{!value.push.configured&&<p className="form-note">Workspace notification keys need setup. See the local notification setup instructions.</p>}<button className="secondary" disabled={busy||!value.push_enabled||!value.devices.some(d=>d.current&&d.enabled)} onClick={()=>void run(async()=>{await api('/api/v1/notifications/test',{method:'POST'});setMessage('Test queued. Quiet hours apply; push service acceptance does not confirm that the operating system displayed it.');})}>Send test notification</button></section>
      <form className="settings-form" onSubmit={event=>{event.preventDefault();void run(async()=>{await save(value);setMessage('Notification preferences saved.');});}}>
        <label className="check-row"><input type="checkbox" checked={value.push_enabled} onChange={e=>field('push_enabled',e.target.checked)}/>Allow push to registered browsers</label>
        <fieldset><legend>Notify me about</legend>{[['questions','Questions'],['reviews','Reviews'],['results','Results'],['problems','Problems and setup']].map(([key,label])=><label className="check-row" key={key}><input type="checkbox" checked={value.categories.includes(key)} onChange={e=>field('categories',e.target.checked?[...value.categories,key]:value.categories.filter(c=>c!==key))}/>{label}</label>)}</fieldset>
        <fieldset><legend>Quiet hours</legend><label className="check-row"><input type="checkbox" checked={value.quiet_enabled} onChange={e=>field('quiet_enabled',e.target.checked)}/>Pause all browser notifications during quiet hours</label><div className="form-grid"><label>From<input type="time" value={value.quiet_start} onChange={e=>field('quiet_start',e.target.value)} required/></label><label>Until<input type="time" value={value.quiet_end} onChange={e=>field('quiet_end',e.target.value)} required/></label></div><label>Timezone<input aria-label="Notification timezone" value={value.timezone} onChange={e=>field('timezone',e.target.value)} required maxLength={100}/><small>Use an IANA timezone, such as Asia/Kolkata. Travel does not change this automatically.</small></label><p className="form-note">Work continues and questions keep their real expiry. Fresh unread updates are grouped when quiet hours end.</p></fieldset>
        <button className="primary" disabled={busy}>Save preferences</button>
      </form>
      <section className="notification-devices"><h3>Registered browsers</h3>{!value.devices.length?<p className="form-note">No browsers registered yet.</p>:value.devices.map(device=><div className="notification-device" key={device.id}><span><strong>{device.label}{device.current?' · current':''}</strong><small>{device.enabled?device.status.replaceAll('_',' '):'Disabled'} · expires {new Date(device.expires_at).toLocaleDateString()}</small></span>{device.enabled&&<button className="secondary" disabled={busy} onClick={()=>void run(async()=>{await api(`/api/v1/notifications/subscriptions/${device.id}`,{method:'DELETE'});if(device.current)await clearBrowserPush();})}>Disable</button>}</div>)}</section>
    </>}
  </>;
}
