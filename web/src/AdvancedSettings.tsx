import {useEffect,useState,type FormEvent} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api} from './api';

type Values={takeover_unpausable:'ask'|'refuse'|'continue';takeover_reply_wait_seconds:0|15|30|60};
type Setting=keyof Values;
type Advanced={values:Values;defaults:Values;history:{setting:Setting;from:string|number;to:string|number;changed_at:string}[]};
const SETTINGS:{[K in Setting]:{label:string;choices:[Values[K],string,string][]}}={
  takeover_unpausable:{label:'When the bot can’t be paused for a takeover',choices:[
    ['ask','Ask me','Show “Couldn’t pause the bot” with Try again, Take over without pausing, or Cancel.'],
    ['refuse','Always refuse','Give control back to the bot and ask you to try again.'],
    ['continue','Always take over without pausing','Let you in while the bot keeps working. It still can’t see your private browser.']]},
  takeover_reply_wait_seconds:{label:'When the bot is mid-reply at takeover',choices:[
    [0,'Pause immediately','You’re in at once; the interrupted reply is redone after you hand back.'],
    [15,'Wait up to 15 seconds','Let the reply finish first, so nothing is redone. Take over now skips the wait.'],
    [30,'Wait up to 30 seconds','Let the reply finish first, so nothing is redone. Take over now skips the wait.'],
    [60,'Wait up to 60 seconds','Let the reply finish first, so nothing is redone. Take over now skips the wait.']]},
};
const choiceLabel=(setting:Setting,value:string|number)=>SETTINGS[setting].choices.find(([choice])=>choice===value)?.[1]||String(value);

// The Advanced section of Settings: workspace-wide behaviour whose defaults are recommended.
export function AdvancedSettings({active}:{active:boolean}){
  const query=useQueryClient();
  const advanced=useQuery({queryKey:['advanced-settings'],queryFn:()=>api<Advanced>('/api/v1/settings/advanced'),enabled:active});
  const [form,setForm]=useState<Values|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
  useEffect(()=>{if(active){setForm(null);setError('');setMessage('');}},[active]);
  const data=advanced.data,values=form||data?.values;
  async function run(work:()=>Promise<Advanced>,done:string){setBusy(true);setError('');setMessage('');try{query.setQueryData(['advanced-settings'],await work());setForm(null);setMessage(done);}catch(err){setError((err as Error).message);}finally{setBusy(false);}}
  function save(event:FormEvent){event.preventDefault();if(values)void run(()=>api<Advanced>('/api/v1/settings/advanced',{method:'PUT',body:JSON.stringify(values)}),'Advanced settings saved.');}
  if(!data||!values)return advanced.error?<p role="alert" className="error-text">{advanced.error.message}</p>:<p>Loading advanced settings…</p>;
  const changed=(Object.keys(SETTINGS) as Setting[]).some(key=>values[key]!==data.values[key]);
  const defaults=(Object.keys(SETTINGS) as Setting[]).every(key=>data.values[key]===data.defaults[key]);
  return <>
    <p className="advanced-warning" role="note">These change how Hermbot behaves for the whole workspace. The defaults are recommended.</p>
    {error&&<p role="alert" className="error-text">{error}</p>}{message&&<p role="status" className="form-note">{message}</p>}
    <form className="settings-form" onSubmit={save}>
      <h4 className="advanced-group">Computer takeover</h4>
      {(Object.keys(SETTINGS) as Setting[]).map(key=><fieldset key={key}><legend>{SETTINGS[key].label}</legend>
        {SETTINGS[key].choices.map(([choice,label,note])=><label className="check-row advanced-choice" key={String(choice)}>
          <input type="radio" name={key} checked={values[key]===choice} onChange={()=>setForm({...values,[key]:choice})}/>
          <span><strong>{label}{choice===data.defaults[key]?' · default':''}</strong><small>{note}</small></span></label>)}
      </fieldset>)}
      <div className="button-row"><button className="primary" disabled={busy||!changed}>Save advanced settings</button>
        <button type="button" className="secondary" disabled={busy||defaults} onClick={()=>void run(()=>api<Advanced>('/api/v1/settings/advanced/reset',{method:'POST'}),'Advanced settings reset to their defaults.')}>Reset to defaults</button></div>
    </form>
    <section className="advanced-history"><h4>Recent changes</h4>
      {data.history.length?<ul>{data.history.map((row,index)=><li key={index}><span>{SETTINGS[row.setting]?.label||row.setting}: {choiceLabel(row.setting,row.from)} → {choiceLabel(row.setting,row.to)}</span><small>{new Date(row.changed_at).toLocaleString()}</small></li>)}</ul>:<p className="form-note">No changes yet.</p>}
    </section>
  </>;
}
