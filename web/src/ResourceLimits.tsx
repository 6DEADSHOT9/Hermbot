import {useEffect,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api,requestKey} from './api';

type Key='pids_limit'|'nofile_hard'|'allow_swap'|'log_size_mb'|'log_files';
type Limit={value:number|boolean;source:'computed'|'owner'|'workspace';computed_value:number|boolean;recommended:[number,number];hard:[number,number];formula:string;reason:string;enforcement:string;applies:string;uncertain:boolean;available:boolean};
type Machine={cpus:number|null;memory_bytes:number|null;rootless:boolean|null;logging_driver:string;cgroup_version:string|null;observed_at:string|null;warnings:string[];operating_system?:string;pid_max?:number|null;threads_max?:number|null;nr_open?:number|null;file_max?:number|null;disk_free_bytes?:number|null;disk_total_bytes?:number|null};
type LimitsView={revision:number;machine_signature:string;machine:Machine;limits:Record<Key,Limit>;resources:{memory_min_mb:number;memory_max_mb:number;memory_default_mb:number;memory_options_mb:number[];cpus_min:number;cpus_max:number;cpus_default:number;memory_uncertain?:boolean;cpus_uncertain?:boolean};capacity:{capacity:number|null;limiting_resources:string[];memory_today_mb:number|null;memory_at_max_mb:number|null;memory_total_mb:number|null;max_bots:number;max_active:number;above_capacity:boolean|null;uncertain:boolean};disk_preview:{active:boolean;enforcement:string;uncertain:boolean;default_gb:number;hard_max_gb:number;capacity:number|null};bots:{id:string;name:string;status:string;desired_state:string;pending:boolean;observed:Record<string,unknown>|null;override_errors:string[];resource_errors:string[];affected_tasks?:string[]}[];history:{setting:Key;from:number|boolean|null;to:number|boolean|null;changed_at:string;accepted_warnings?:string[]}[];core_services_pending_restart:string[]};
type Draft=Partial<Record<Key,number|boolean|null>>;
type LimitsUpdate={values:Record<string,number|boolean|null>;expected_revision:number;machine_signature:string;accepted_warnings:string[];apply_now:boolean};
const FIELDS:{key:Key;label:string;unit:string}[]=[
  {key:'pids_limit',label:'Processes and threads per teammate',unit:'processes'},
  {key:'nofile_hard',label:'Open files per process',unit:'files'},
  {key:'allow_swap',label:'Memory swap',unit:''},
  {key:'log_size_mb',label:'Log file size',unit:'MB'},
  {key:'log_files',label:'Log files kept',unit:'files'},
];
const labels:Record<Key,string>={pids_limit:'Processes and threads per teammate',nofile_hard:'Open files per process',allow_swap:'Memory swap',log_size_mb:'Log file size',log_files:'Log files kept'};
const display=(value:number|boolean|null|undefined,key?:Key)=>value===null||value===undefined?'Computed default':key==='allow_swap'?(value?'Allowed':'Off'):String(value);
const gb=(mb:number|null|undefined)=>mb==null?'Unknown':`${(mb/1024).toFixed(1)} GB`;
const signature=(value:Partial<LimitsView>|undefined):value is LimitsView=>!!value&&typeof value.revision==='number'&&typeof value.machine_signature==='string'&&!!value.machine&&Array.isArray(value.machine.warnings)&&!!value.limits&&FIELDS.every(({key})=>!!value.limits?.[key])&&!!value.resources&&!!value.capacity&&Array.isArray(value.capacity.limiting_resources)&&!!value.disk_preview&&Array.isArray(value.bots)&&Array.isArray(value.history)&&Array.isArray(value.core_services_pending_restart);

export function ResourceLimits({active,botId}:{active?:boolean;botId?:string}){
  const query=useQueryClient();
  const path=botId?`/api/v1/bots/${botId}/limits`:'/api/v1/settings/limits';
  const queryKey=['resource-limits',botId||'workspace'];
  const request=useQuery({queryKey,queryFn:()=>api<LimitsView>(path),enabled:active??true});
  const [draft,setDraft]=useState<Draft>({}),[warnings,setWarnings]=useState<string[]|null>(null),[confirmationPayload,setConfirmationPayload]=useState<LimitsUpdate|null>(null),[applyNow,setApplyNow]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
  useEffect(()=>{setDraft({});setWarnings(null);setConfirmationPayload(null);setApplyNow(false);setError('');setMessage('');},[botId]);
  const data=signature(request.data)?request.data:null;
  if(active===false)return null;
  if(request.isLoading)return <p>Loading {botId?'teammate':'machine and container'} limits…</p>;
  if(request.error||!data)return <p className="form-note">{botId?'Teammate process limits':'Machine and container limits'} are unavailable.</p>;
  const changed=(Object.keys(draft) as Key[]).some(key=>draft[key]!==undefined&&draft[key]!==data.limits[key].value);
  const affected=data.bots.filter(bot=>bot.status==='active'&&bot.desired_state==='running'&&(!botId||bot.id===botId));
  const fields=botId?FIELDS.filter(field=>field.key==='pids_limit'):FIELDS;
  const enforcement=(value:string)=>value==='unverified'||value==='configured'?'Enforcement unverified':value==='not_enforced'?'Not enforced here':value==='managed_externally'?'Managed outside Docker':value.replaceAll('_',' ');
  async function save(accepted:string[]=[],confirmedPayload?:LimitsUpdate){
    if(!data)return;
    setBusy(true);setError('');setMessage('');
    try{
      const values:Record<string,number|boolean|null>={};
      for(const key of Object.keys(draft) as Key[]){const value=draft[key];if(value!==undefined&&value!==data.limits[key].value)values[key]=value;}
      const payload=confirmedPayload||{values,expected_revision:data.revision,machine_signature:data.machine_signature,accepted_warnings:accepted,apply_now:applyNow};
      const body={...payload,accepted_warnings:accepted};
      const result=await api<LimitsView&{requires_confirmation?:boolean;warnings?:string[]}>(path,{method:'PUT',headers:{'Idempotency-Key':requestKey()},body:JSON.stringify(body)});
      if(result.requires_confirmation){setWarnings(result.warnings||[]);setConfirmationPayload(payload);return;}
      query.setQueryData(queryKey,result);setDraft({});setWarnings(null);setConfirmationPayload(null);setApplyNow(false);setMessage(botId?'Teammate process limit saved.':'Machine and container settings saved.');
    }catch(err){const failure=err as Error&{status?:number};if(failure.status===409){setWarnings(null);setConfirmationPayload(null);await request.refetch();}setError(failure.message);}
    finally{setBusy(false);}
  }
  const edit=(key:Key,value:number|boolean|null)=>{setDraft(current=>({...current,[key]:value}));setWarnings(null);setConfirmationPayload(null);setError('');};
  return <section className="settings-form resource-limits">
    <h4 className="advanced-group">{botId?'Processes and threads':'Machine and containers'}</h4>
    <p className="form-note">{botId?'This teammate’s process limit applies when its runtime is recreated.':'Container limits are applied when each teammate is idle and restarted. Container configuration is inspected after recreation; kernel enforcement may still be unverified.'}</p>
    {!botId&&<>
    <section className="advanced-history"><h4>This machine</h4>
      <p className="form-note">{data.machine.cpus??'Unknown'} CPUs · {gb(data.machine.memory_bytes==null?null:data.machine.memory_bytes/1024/1024)} memory · {data.machine.rootless===null?'Rootless status unknown':data.machine.rootless?'Rootless Docker':'Rootful Docker'} · cgroup {data.machine.cgroup_version||'unknown'} · log driver {data.machine.logging_driver||'unknown'}</p>
      <p className="form-note">Host ceilings: PIDs {data.machine.pid_max??'Unknown'} · threads {data.machine.threads_max??'Unknown'} · open files/process {data.machine.nr_open??'Unknown'} · system open files {data.machine.file_max??'Unknown'} · free disk {gb(data.machine.disk_free_bytes==null?null:data.machine.disk_free_bytes/1024/1024)} / {gb(data.machine.disk_total_bytes==null?null:data.machine.disk_total_bytes/1024/1024)}</p>
      {data.machine.observed_at&&<small>Read {new Date(data.machine.observed_at).toLocaleString()}</small>}
      {!!data.machine.warnings.length&&<ul>{data.machine.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul>}
    </section>
    </>}
    <div className="settings-form">{fields.map(field=>{
      const limit=data.limits[field.key],value=draft[field.key]===undefined?limit.value:draft[field.key];
      return <fieldset key={field.key}>
        <legend>{field.label}</legend>
        {field.key==='allow_swap'?<label className="check-row"><input type="checkbox" disabled={busy||!limit.available} checked={value===true} onChange={event=>edit(field.key,event.target.checked)}/>Allow swap for teammate containers</label>:<label>{field.label}<input aria-label={field.label} type="number" min={limit.hard[0]} max={limit.hard[1]} step="1" disabled={busy||!limit.available} value={typeof value==='number'?value:''} placeholder={String(limit.computed_value)} onChange={event=>edit(field.key,event.target.value===''?null:Number(event.target.value))}/></label>}
        <p className="form-note">{limit.reason} Recommended {field.key==='allow_swap'?'off':`${limit.recommended[0]}–${limit.recommended[1]} ${field.unit}`}; allowed {field.key==='allow_swap'?'off or on':`${limit.hard[0]}–${limit.hard[1]} ${field.unit}`}. Current: {display(limit.value,field.key)}{field.unit&&field.key!=='allow_swap'?` ${field.unit}`:''} · {limit.source==='owner'?'Owner setting':limit.source==='workspace'?'Workspace setting':'Computed default'} · {enforcement(limit.enforcement)} · {limit.applies==='recreate'?(botId?'Save when idle, or explicitly interrupt affected work.':'Pending while busy; applies after an idle recreate'):''}</p>
        {typeof limit.value==='number'&&(limit.value<limit.hard[0]||limit.value>limit.hard[1])&&<p className="error-text" role="alert">Saved value {limit.value} is outside the current hard range ({limit.hard[0]}–{limit.hard[1]}). Choose an allowed value before applying this setting.</p>}
        <details><summary>How this value is chosen</summary><p className="form-note">Computed default: {display(limit.computed_value,field.key)}. {limit.formula}{limit.source==='owner'?' The saved owner value takes precedence.':limit.source==='workspace'?' The workspace value takes precedence.':''}{limit.uncertain?' Machine reading is uncertain, so the computed value may change.':''}</p></details>
        {limit.source==='owner'&&<button type="button" className="text-button" disabled={busy} onClick={()=>edit(field.key,null)}>{botId?'Use workspace default':'Use computed default'}</button>}
      </fieldset>;
    })}</div>
    {!botId&&!!data.bots.length&&<section className="advanced-history"><h4>Teammate rollout</h4><ul>{data.bots.map(bot=><li key={bot.id}><span>{bot.name}</span><small>{bot.pending?'Pending until idle':bot.observed&&Object.keys(bot.observed).length?'Container settings observed; enforcement unverified':'No pending apply; container observation unavailable'}</small>{!!bot.override_errors.length&&<small className="error-text" role="alert">{bot.override_errors.join(' ')}</small>}{!!bot.resource_errors.length&&<small className="error-text" role="alert">{bot.resource_errors.join(' ')}</small>}</li>)}</ul></section>}
    {!botId&&<>
    <section className="advanced-history"><h4>Capacity estimate</h4>
      <p className="form-note">{data.capacity.capacity===null?'Capacity is unknown':`Estimated capacity: ${data.capacity.capacity} teammates`}{data.capacity.limiting_resources.length?` · limited by ${data.capacity.limiting_resources.join(' and ')}`:''}. This is an estimate, not a guarantee.</p>
      <p className="form-note">Your bots today: {gb(data.capacity.memory_today_mb)} · At your maximum ({data.capacity.max_bots} bots): {gb(data.capacity.memory_at_max_mb)} · Docker memory: {gb(data.capacity.memory_total_mb)}</p>
      {data.capacity.above_capacity&&<p className="advanced-warning" role="note">Your maximum teammate count is above this machine’s estimated capacity.</p>}
    </section>
    <section className="advanced-history"><h4>Disk preview</h4><p className="form-note">No filesystem limit or guarantee is active; disk controls are planned for A2. Proposed default {data.disk_preview.default_gb} GB per teammate and hard maximum {data.disk_preview.hard_max_gb} GB. {data.disk_preview.uncertain?'The disk reading is unavailable, so this preview uses a fallback.':'This estimate does not enforce a disk limit.'}</p></section>
    {!!data.core_services_pending_restart.length&&<p className="advanced-warning" role="note">Pending restart to apply core service settings: {data.core_services_pending_restart.join(', ')}.</p>}
    {!!data.history.length&&<section className="advanced-history"><h4>Resource limit changes</h4><ul>{data.history.map((row,index)=><li key={index}><span>{labels[row.setting]||row.setting}: {display(row.from,row.setting)} → {display(row.to,row.setting)}</span><small>{new Date(row.changed_at).toLocaleString()}{row.accepted_warnings?.length?' · confirmed warning':''}</small></li>)}</ul></section>}
    </>}
    {applyNow&&<p className="advanced-warning" role="note">Applying now stops work and restarts these teammates: {affected.length?affected.map(bot=>bot.name).join(', '):'None'}{affected.reduce((total,bot)=>total+(bot.affected_tasks?.length||0),0)?`. It will stop ${affected.reduce((total,bot)=>total+(bot.affected_tasks?.length||0),0)} active work items.`:''}</p>}
    {!!affected.length&&<label className="check-row"><input type="checkbox" disabled={busy} checked={applyNow} onChange={event=>{setApplyNow(event.target.checked);setWarnings(null);setConfirmationPayload(null);}}/>Apply now and interrupt active work on the affected teammates</label>}
    {warnings&&<section className="advanced-warning" aria-label="Warnings to confirm"><strong>Review these warnings before saving</strong><ul>{warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul><button type="button" className="primary" disabled={busy} onClick={()=>void save(warnings,confirmationPayload||undefined)}>{confirmationPayload?.apply_now?'Confirm and interrupt listed teammates':'Confirm and save these settings'}</button></section>}
    {error&&<p role="alert" className="error-text">{error}</p>}{message&&<p role="status" className="form-note">{message}</p>}
    <div className="button-row"><button type="button" className="primary" disabled={busy||!changed||!!warnings} onClick={()=>void save()}>{applyNow?'Review apply now':botId?'Save process limit':'Save machine and container settings'}</button></div>
  </section>;
}
