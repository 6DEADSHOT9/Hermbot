import {useState} from 'react';
import {useQueryClient} from '@tanstack/react-query';
import {mutate, type Grant, type RecurringManagementSettings, type State} from './api';

const permissions = {delegate:'Assign work',inspect:'Inspect results',steer:'Redirect work',cancel:'Stop owned work',reassign:'Reassign work'};

export function RecurringManagement({state,manager,value,onChange}:{state?:State;manager:string;value?:RecurringManagementSettings|null;onChange:(value:RecurringManagementSettings|null)=>void}) {
  const cache=useQueryClient();
  const [hours,setHours]=useState(24),[busy,setBusy]=useState(false),[error,setError]=useState(''),[created,setCreated]=useState<Grant|null>(null);
  const grants=[...(state?.grants||[]),...(created?[created]:[])].filter((g,i,a)=>a.findIndex(x=>x.id===g.id)===i&&g.kind==='standing'&&g.manager_id===manager&&!g.revoked_at&&new Date(g.expires_at)>new Date()&&g.operations.includes('delegate'));
  const selected=grants.find(g=>g.id===value?.grant_id);
  const bots=(state?.bots||[]).filter(b=>b.id!==manager&&b.status==='active'&&(!selected||selected.targets.includes(b.id)));
  function set(change:Partial<RecurringManagementSettings>) {if(value){setError('');onChange({...value,...change});}}
  async function create() {
    if(!value)return;
    setBusy(true);setError('');
    try {
      const grant=await mutate<Grant>('/api/v1/grants','POST',{manager_id:manager,targets:value.targets,operations:value.operations,duration_hours:hours,max_created_bots:0,max_memory_mb:512,max_cpus:0.25});
      setCreated(grant);set({grant_id:grant.id});
      await cache.invalidateQueries({queryKey:['state']});
    } catch(e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  return <fieldset className="recurring-management">
    <legend>Team coordination</legend>
    <label className="check-row"><input type="checkbox" checked={!!value} onChange={e=>{setError('');onChange(e.target.checked?{grant_id:'',targets:[],operations:Object.keys(permissions)}:null);}}/>Coordinate teammates</label>
    {value&&<>
      <label>Management permission<select aria-label="Recurring management permission" value={value.grant_id} onChange={e=>{const grant=grants.find(g=>g.id===e.target.value);set({grant_id:e.target.value,targets:[],operations:grant?Object.keys(permissions).filter(op=>grant.operations.includes(op)):Object.keys(permissions)});}}>
        <option value="">Create a permission below</option>
        {value.grant_id&&!selected&&<option value={value.grant_id}>Permission unavailable — choose another</option>}
        {grants.map(g=><option key={g.id} value={g.id}>Manage {g.targets.map(id=>state?.bots.find(b=>b.id===id)?.name||id).join(', ')} · until {new Date(g.expires_at).toLocaleDateString()}</option>)}
      </select></label>
      <fieldset><legend>Collaborators for each run</legend>
        {bots.map(bot=><label className="check-row" key={bot.id}><input type="checkbox" checked={value.targets.includes(bot.id)} onChange={e=>set({targets:e.target.checked?[...value.targets,bot.id]:value.targets.filter(id=>id!==bot.id)})}/>{bot.name}{bot.desired_state!=='running'?' · stopped':''}</label>)}
        {!bots.length&&<p className="form-note">No eligible collaborators. Choose another permission or create and start a teammate in Workspace settings.</p>}
        {value.targets.some(id=>!bots.some(b=>b.id===id))&&<p className="error-text">A selected collaborator is unavailable. Select an available teammate before saving.</p>}
      </fieldset>
      <details><summary>Management operations</summary>{Object.entries(permissions).filter(([op])=>!selected||selected.operations.includes(op)).map(([op,label])=><label className="check-row" key={op}><input type="checkbox" checked={value.operations.includes(op)} disabled={op==='delegate'} onChange={e=>set({operations:e.target.checked?[...value.operations,op]:value.operations.filter(v=>v!==op)})}/>{label}</label>)}</details>
      {!value.grant_id&&<div className="routine-permission-create"><label>Permission duration (hours)<input aria-label="Recurring permission duration" type="number" min={1} max={720} value={hours} onChange={e=>setHours(Number(e.target.value))}/></label><button type="button" className="secondary" disabled={busy||!value.targets.length||hours<1||hours>720} onClick={create}>{busy?'Creating permission…':'Create management permission'}</button></div>}
      {selected&&<p className="form-note">Authorized until {new Date(selected.expires_at).toLocaleString()}. Only selected collaborators participate. Permission is checked again for every run and action.</p>}
      {!!value.grant_id&&!selected&&<p className="error-text">This permission expired, was revoked, or belongs to another manager. Choose valid permission before saving.</p>}
      <p className="form-note">The active work budget is shared across the manager and collaborators. Connected-app changes keep their existing review rules.</p>
      {error&&<p role="alert" className="error-text">{error}</p>}
    </>}
  </fieldset>;
}
