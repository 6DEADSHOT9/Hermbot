import {useEffect,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api,mutate,type Bot,type Task} from './api';
import {Panel} from './Management';
import {Teaching} from './Teaching';
import {usePanelScope} from './usePanelScope';
import {CapabilitySummary,type CapabilityView} from './Capabilities';

type Memory={name:string;content:string;sha256:string;limit:number};
type Version={id:string;kind:string;name:string;version:number;sha256:string;created_at:string;content?:string};
type Skill={name:string;description:string;enabled:boolean;required_tools?:string[];metadata_error?:string};
type KnowledgeState={memory:Memory[];skills:Skill[];versions:Version[]};
type Draft={kind:'memory'|'skill';name:string;content:string;expected_hash:string;enable?:boolean;source_task_id?:string;editable?:boolean};
const emptyHash='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
const newSkill=(name:string)=>`---\nname: ${name}\ndescription: Describe when to use this skill\n---\n\n# Procedure\n\nDescribe the steps, required inputs and checks. Teammate management requires a current owner grant.\n`;

export function Knowledge({open,onClose,bot,tasks,initialTask}:{open:boolean;onClose:()=>void;bot?:Bot;tasks:Task[];initialTask?:string|null}) {
  const query=useQueryClient();
  const [teachingDirty,setTeachingDirty]=useState(false),[discardTeaching,setDiscardTeaching]=useState(false);
  function close(){if(teachingDirty){setDiscardTeaching(true);setError('Save your teaching edits before closing, or explicitly close without saving them.');return;}onClose();}
  const current=usePanelScope(JSON.stringify([open,bot?.id,initialTask]));
  const [draft,setDraft]=useState<Draft|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[search,setSearch]=useState(''),[taskId,setTaskId]=useState('');
  const data=useQuery({queryKey:['knowledge',bot?.id],queryFn:()=>api<KnowledgeState>(`/api/v1/bots/${bot!.id}/knowledge`),enabled:open&&!!bot&&bot.status==='active',refetchInterval:open?5000:false});
  const capabilities=useQuery({queryKey:['capabilities',bot?.id],queryFn:()=>api<CapabilityView>(`/api/v1/bots/${bot!.id}/capabilities`),enabled:open&&!!bot,refetchInterval:open?5000:false});
  useEffect(()=>{if(open){setDraft(null);setError('');setNotice('');setBusy(false);setSearch('');setTaskId(initialTask||'');if(initialTask)loadWorkflow(initialTask);}},[open,bot?.id,initialTask]);
  const healthy=bot?.status==='active'&&bot.desired_state==='running';
  async function change(work:()=>Promise<unknown>){setBusy(true);setError('');setNotice('');try{await work();await query.invalidateQueries({queryKey:['state']});await query.invalidateQueries({queryKey:['knowledge',bot?.id]});await query.invalidateQueries({queryKey:['skills']});}catch(err){if(current())setError((err as Error).message);}finally{if(current())setBusy(false);}}
  async function selectSkill(name:string){await change(async()=>{const result=await api<{content:string;sha256:string;editable:boolean}>(`/api/v1/bots/${bot!.id}/knowledge/skills/${name}`);if(!current())return;setDraft({kind:'skill',name,content:result.content,expected_hash:result.sha256,enable:bot!.enabled_skills.includes(name),editable:result.editable});});}
  async function save(content?:string){if(!draft||!bot)return;await change(async()=>{await mutate(`/api/v1/bots/${bot.id}/knowledge/${draft.kind}/${draft.name}`,'PUT',{content:content??draft.content,expected_hash:draft.expected_hash,...(draft.kind==='skill'?{enable:content===''?false:draft.enable||false}:{}),...(draft.source_task_id?{source_task_id:draft.source_task_id}:{})});if(!current())return;setDraft(null);setNotice('Saved. The runtime is refreshing so its next run sees the new knowledge.');});}
  async function loadWorkflow(id:string){if(!id)return;await change(async()=>{const result=await api<{name:string;content:string;expected_hash:string;source_task_id:string}>(`/api/v1/tasks/${id}/skill-draft`);if(!current())return;setDraft({kind:'skill',...result,enable:false,editable:true});});}
  async function importSkill(file:File){await change(async()=>{if(file.size>100_000)throw new Error('Imported skill Markdown is limited to 100 KB');let content=await file.text();const existing=content.match(/^name:\s*(.+)$/m)?.[1].trim().replace(/^['"]|['"]$/g,'')||`import-${Date.now().toString(36)}`;const name=existing.startsWith('hermbot-')?existing:`hermbot-${existing.replace(/[^a-z0-9-]/gi,'-').toLowerCase()}`;if(content.startsWith('---\n'))content=content.replace(/^name:\s*.+$/m,`name: ${name}`);else content=newSkill(name)+ '\n'+content;if(!current())return;setDraft({kind:'skill',name,content,expected_hash:emptyHash,enable:false,editable:true});});}
  return <Panel open={open} onClose={close} title={`${bot?.name||'Teammate'} · Memory & skills`} description="Review native memory, manage skill versions, and turn completed work into a reusable procedure.">
    {discardTeaching&&<button className="secondary" onClick={()=>{setDiscardTeaching(false);onClose();}}>Close without saving teaching edits</button>}
    {error&&<p role="alert" className="error-text">{error}</p>}{notice&&<p role="status">{notice}</p>}{data.error&&<p role="alert" className="error-text">{data.error.message}</p>}
    {!healthy&&<p>{bot?.status==='provisioning'?'The runtime is refreshing. Wait for it to finish before editing knowledge.':'Start this teammate and wait for provisioning to finish before editing knowledge.'}</p>}
    <CapabilitySummary view={capabilities.data}/>
    {bot&&open&&<Teaching key={bot.id} botId={bot.id} mine={false} onDirty={setTeachingDirty}/>}
    {draft?<div className="knowledge-draft">
      <label>{draft.kind==='skill'?'Skill name':'Memory target'}<input aria-label="Knowledge name" value={draft.name} disabled={draft.kind==='memory'||!draft.editable||draft.expected_hash!==emptyHash} onChange={e=>{const name=e.target.value;setDraft({...draft,name,content:draft.content.replace(/^name:\s*.+$/m,`name: ${name}`)});}}/></label>
      <label>Content<textarea aria-label="Knowledge content" value={draft.content} onChange={e=>setDraft({...draft,content:e.target.value})} readOnly={draft.kind==='skill'&&!draft.editable} rows={14} maxLength={100_000}/></label>
      {draft.kind==='memory'&&<small>{draft.content.length} / {data.data?.memory.find(m=>m.name===draft.name)?.limit} native characters. Emptying the content removes this memory target.</small>}
      {draft.kind==='skill'&&<label className="check-row"><input type="checkbox" checked={draft.enable||false} disabled={!draft.editable} onChange={e=>setDraft({...draft,enable:e.target.checked})}/>Enable this skill after saving</label>}
      {draft.kind==='skill'&&!draft.editable&&<p>Bundled skills are preserved. Import a copy to create an editable owner-managed version.</p>}
      <div className="button-row">{(draft.kind==='memory'||draft.editable)&&<><button className="primary" disabled={busy||!healthy} onClick={()=>save()}>Save reviewed content</button><button className="secondary" disabled={busy||!healthy} onClick={()=>save('')}>Remove {draft.kind==='memory'?'memory':'skill'}</button></>}<button className="secondary" onClick={()=>setDraft(null)}>Back</button></div>
      <label>Saved versions<select aria-label="Knowledge version" value="" onChange={e=>{if(e.target.value)change(async()=>{const v=await api<Version>(`/api/v1/bots/${bot!.id}/knowledge/versions/${e.target.value}`);if(!current())return;setDraft({...draft,content:v.content||''});setNotice('Historical content loaded as a draft. Saving creates a new version against the current native hash.');});}}><option value="">Load a version as a new draft</option>{data.data?.versions.filter(v=>v.kind===draft.kind&&v.name===draft.name).map(v=><option key={v.id} value={v.id}>v{v.version} · {new Date(v.created_at).toLocaleString()}</option>)}</select></label>
    </div>:<>
      <h3>Curated memory</h3><div className="knowledge-memory">{data.data?.memory.map(memory=><button className="artifact-card" key={memory.name} onClick={()=>{setError('');setDraft({kind:'memory',name:memory.name,content:memory.content,expected_hash:memory.sha256});}}><span><strong>{memory.name==='user'?'User profile':'Personal notes'}</strong><small>{memory.content||'No saved entries'}</small></span></button>)}</div>
      <h3>Skills</h3><p className="form-note">Your teammate automatically consults relevant enabled skills for each assignment. Enabling a skill does not change its tool or application access.</p><label>Find a skill<input aria-label="Find installed skill" value={search} onChange={e=>setSearch(e.target.value)}/></label><div className="button-row"><button className="secondary" disabled={!healthy} onClick={()=>{const name='hermbot-new-skill';setDraft({kind:'skill',name,content:newSkill(name),expected_hash:emptyHash,enable:false,editable:true});}}>Create skill</button><label className="secondary library-upload">Import SKILL.md<input aria-label="Import skill Markdown" type="file" accept=".md,text/markdown" disabled={busy||!healthy} onChange={e=>{const file=e.target.files?.[0];if(file)importSkill(file);e.target.value='';}}/></label></div>
      <div className="knowledge-skills">{data.data?.skills.filter(s=>(s.name+' '+s.description).toLowerCase().includes(search.toLowerCase())).map(skill=><div className="knowledge-skill" key={skill.name}><button className="artifact-card" onClick={()=>selectSkill(skill.name)}><span><strong>{skill.name}</strong><small>{skill.description}</small></span></button><button className="secondary" disabled={busy||!healthy} onClick={()=>change(async()=>{await mutate(`/api/v1/bots/${bot!.id}/knowledge/skills/${skill.name}/enabled`,'POST',{enabled:!skill.enabled,expected_revision:bot!.revision});if(!current())return;setNotice('Skill selection saved. Wait for the runtime refresh to finish.');})}>{skill.enabled?'Disable':'Enable'}</button></div>)}</div>
      <h3>Save a completed workflow</h3><label>Completed task<select aria-label="Completed workflow" value={taskId} onChange={e=>setTaskId(e.target.value)}><option value="">Choose completed work</option>{tasks.filter(t=>t.status==='completed'&&!t.parent_id).map(t=><option key={t.id} value={t.id}>{t.input.slice(0,100)}</option>)}</select></label><button className="secondary" disabled={!taskId||busy||!healthy} onClick={()=>loadWorkflow(taskId)}>Review workflow draft</button>
    </>}
  </Panel>;
}
