import { Fragment, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import * as Dialog from '@radix-ui/react-dialog';
import Markdown from 'react-markdown';
import { ArrowUp, ArrowUpRight, BookOpen, Check, ChevronDown, ChevronRight, Circle, Clock3, Ellipsis, FileText, Layers3, LogOut, Menu, Monitor, Moon, Plus, Search, ShieldCheck, Sun, Users, X } from 'lucide-react';
import { api, clearMutationKeys, formatFileSize, mutate, requestKey, executionSummary, settled, statusLabel, taskStatus, type Approval, type Artifact, type Attachment, type Bot, type BotId, type Chat, type Conversation, type Event, type Message, type State, type Task } from './api';
import { BotEditor, LifecycleDialog, Panel, SettingsWindow } from './Management';
import {Library,type LibraryFile} from './Library';
import {Knowledge} from './Knowledge';
import {Computer} from './Computer';
import {Connections} from './Connections';
import {WorkItems} from './WorkItems';
import {TaskPreparation} from './Capabilities';
import {TaskActivity} from './TaskActivity';
import {InputCard} from './InputCard';
import {useTimeline, type TimelineEntry} from './timeline';
import {Routines} from './Routines';
import {Attention,type AttentionTarget,type Inbox} from './Attention';
import {clearBrowserPush} from './Notifications';
import {ConnectorDraftFields,ConnectorPreview} from './ConnectorPreview';

function Avatar({bot, size = 36}: {bot?: Bot; size?: number}) {
  return <span className="avatar" style={{width: size, height: size, background: bot?.color || '#829c77'}} aria-hidden="true">{bot?.avatar||<svg viewBox="0 0 36 36"><path d="M10 14v5m16-5v5" stroke="currentColor" strokeWidth="3" strokeLinecap="round"/><path d="M13 24q5 4 10 0" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/><path d="M7 5l5 3M29 5l-5 3" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></svg>}</span>;
}

function isConversationResponse(task:Task) {
  return !task.parent_id&&task.execution_kind!=='peer_message'&&!['assignment','update'].includes(task.message_intent||'');
}

function Modal({open, onOpenChange, title, description, children}: {open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string; children: ReactNode}) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="modal-overlay"/><Dialog.Content className="modal"><Dialog.Title>{title}</Dialog.Title><Dialog.Description>{description}</Dialog.Description><Dialog.Close className="icon close" aria-label="Close"><X size={18}/></Dialog.Close>{children}</Dialog.Content></Dialog.Portal></Dialog.Root>;
}

export function App() {
  const query = useQueryClient();
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [ownerToken, setOwnerToken] = useState('');
  const [error, setError] = useState('');
  const [active, setActive] = useState('');
  const [draft, setDraft] = useState('');
  const [filter, setFilter] = useState('');
  const [sections,setSections]=useState(()=>{
    const defaults={team:true,past:false,groups:true};
    try{const saved=JSON.parse(localStorage.getItem('hermbot-sidebar-sections')||'null');for(const key of Object.keys(defaults) as (keyof typeof defaults)[]){if(typeof saved?.[key]==='boolean')defaults[key]=saved[key];}}catch{/* Keep navigation usable if browser storage is unavailable. */}
    return defaults;
  });
  const [manager, setManager] = useState(false);
  const [sending, setSending] = useState(false);
  const [target, setTarget] = useState<BotId>('researcher');
  const [sidebar, setSidebar] = useState(false);
  const [contextOpen, setContextOpen] = useState(()=>!window.matchMedia('(max-width: 950px)').matches);
  const [groupOpen, setGroupOpen] = useState(false);
  const [groupTitle, setGroupTitle] = useState('Project chat');
  const [reviewEdit,setReviewEdit]=useState<{id:string;hash:string;summary:string;value:string}|null>(null);
  const [members, setMembers] = useState<BotId[]>(['researcher', 'writer', 'reviewer']);
  const [routinesOpen,setRoutinesOpen]=useState(false);
  const [attentionSettings,setAttentionSettings]=useState<{id:string;kind:string}|null>(null);
  const [attentionOpen,setAttentionOpen]=useState(false);
  const [attentionInitial,setAttentionInitial]=useState<string|null>(()=>new URLSearchParams(location.search).get('attention'));
  const attentionNavigation=useRef<AttentionTarget|null>(null);
  const [attentionConversation,setAttentionConversation]=useState<Conversation|null>(null);
  const [libraryOpen,setLibraryOpen]=useState(false);
  const [libraryInitial,setLibraryInitial]=useState<string|null>(null);
  const [libraryFiles,setLibraryFiles]=useState<LibraryFile[]>([]);
  const [steering, setSteering] = useState<Task | null>(null);
  const [direction, setDirection] = useState('');
  const [theme, setTheme] = useState(() => localStorage.getItem('hermbot-theme') || 'light');
  const [activity, setActivity] = useState<Event[]>([]);
  const source = useRef<EventSource | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const pendingSend = useRef<{fingerprint: string; key: string} | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  const scrollHistory = useRef({scope:'',last:0});
  const restoreAnchor = useRef<{id:string;offset:number;height:number;top:number}|null>(null);
  const [unseen,setUnseen]=useState(false);
  const [announcement,setAnnouncement]=useState('');
  const ownerMenu = useRef<HTMLDetailsElement>(null);
  const [contextTab, setContextTab] = useState<'work' | 'outputs'>('work');
  const [settingsOpen,setSettingsOpen]=useState(false);
  const [connectionsOpen,setConnectionsOpen]=useState(false),[connectionsTab,setConnectionsTab]=useState('Connections');
  const [knowledgeOpen,setKnowledgeOpen]=useState(false);
  const [workflowTask,setWorkflowTask]=useState<string|null>(null);
  const [computerOpen,setComputerOpen]=useState(false);
  const [computerBotId,setComputerBotId]=useState<string|null>(null);
  const [botEditor,setBotEditor]=useState<{bot?:Bot;clone?:boolean}|null>(null);
  const [lifecycle,setLifecycle]=useState<{bot:Bot;action:string}|null>(null);
  const [thread,setThread]=useState<Message|null>(null);
  const activeRef=useRef(active);activeRef.current=active;
  const chatScope=useRef({active,threadId:thread?.id,generation:0});
  if(chatScope.current.active!==active||chatScope.current.threadId!==thread?.id)chatScope.current={active,threadId:thread?.id,generation:chatScope.current.generation+1};
  const [attachments,setAttachments]=useState<Attachment[]>([]);
  const [mentions,setMentions]=useState<BotId[]>([]);
  const [lifecycleAuthority,setLifecycleAuthority]=useState(false);
  const [standingGrant,setStandingGrant]=useState('');
  const [inspect,setInspect]=useState<Task|null>(null);
  const [editTask,setEditTask]=useState<Task|null>(null);
  const [editPrompt,setEditPrompt]=useState('');
  const [proposalEdit,setProposalEdit]=useState<{id:string;value:string}|null>(null);
  const [managedTargets,setManagedTargets]=useState<BotId[]>([]);
  const [editingGroup,setEditingGroup]=useState<Conversation|null>(null);

  const state = useQuery({queryKey: ['state'], queryFn: () => api<State>('/api/v1/state'), enabled: authenticated === true, refetchInterval: 5000});
  const chat = useTimeline(active,thread?.id,authenticated===true);
  const attentionCounts=useQuery({queryKey:['attention-counts'],queryFn:()=>api<Inbox>('/api/v1/attention?limit=1'),enabled:authenticated===true,refetchInterval:5000});
  const data = state.data;
  const conversation = data?.conversations.find(item => item.id === active)|| (attentionConversation?.id===active?attentionConversation:undefined);
  const bot = [...(data?.bots||[]),...(data?.retired_bots||[])].find(item => item.id === (conversation?.bot_id || target));
  const tasks = data?.tasks.filter(task => task.conversation_id === active) || [];
  const workItems=data?.work_items?.filter(w=>w.conversation_id===active)||[];
  const visibleWork=workItems.filter(w=>!w.hidden_at&&w.current_task);
  const ongoing = tasks.filter(task => !settled(task) && task.status !== 'paused_budget');
  const backgroundWork = ongoing.filter(task => !isConversationResponse(task));
  const outputs = chat.data?.artifacts || data?.artifacts.filter(artifact => tasks.some(task => task.id === artifact.task_id)) || [];
  const botById = (id: string) => data?.bots.find(item => item.id === id)||data?.retired_bots.find(item=>item.id===id);
  const availableBots=data?.bots.filter(item=>item.status!=='archived'&&item.status!=='deleted')||[];
  const pastConversations=data?.conversations.filter(c=>c.kind==='dm'&&!availableBots.some(b=>b.id===c.bot_id))||[];
  const entries=chat.data?.entries||[];
  const sectionOpen=(section:keyof typeof sections)=>sections[section]||!!filter.trim();
  function toggleSection(section:keyof typeof sections){setSections(current=>({...current,[section]:!current[section]}));}
  useEffect(()=>{try{localStorage.setItem('hermbot-sidebar-sections',JSON.stringify(sections));}catch{/* Navigation still works without persisted preferences. */}},[sections]);

  useEffect(()=>{const media=window.matchMedia('(max-width: 950px)');const resized=()=>{if(media.matches)setContextOpen(false);};media.addEventListener('change',resized);return()=>media.removeEventListener('change',resized);},[]);
  useEffect(()=>{const close=(event:KeyboardEvent)=>{if(event.key==='Escape'&&!document.querySelector('[role="dialog"]')){ownerMenu.current?.removeAttribute('open');setSidebar(false);if(window.matchMedia('(max-width: 950px)').matches)setContextOpen(false);}};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[]);
  useEffect(()=>{const close=(event:PointerEvent)=>{if(ownerMenu.current&&!ownerMenu.current.contains(event.target as Node))ownerMenu.current.open=false;};document.addEventListener('pointerdown',close);return()=>document.removeEventListener('pointerdown',close);},[]);

  useEffect(() => {api<State>('/api/v1/state').then(() => setAuthenticated(true)).catch(() => setAuthenticated(false));}, []);
  useEffect(()=>{const expired=()=>{source.current?.close();source.current=null;query.clear();clearMutationKeys();setActive('');setActivity([]);setAuthenticated(false);};window.addEventListener('hermbot-session-expired',expired);return()=>window.removeEventListener('hermbot-session-expired',expired);},[query]);
  useEffect(() => {document.documentElement.dataset.theme = theme; localStorage.setItem('hermbot-theme', theme);}, [theme]);
  useEffect(() => {if(data&&(!active||!conversation))setActive(data.conversations.find(c=>c.kind==='group'||availableBots.some(b=>b.id===c.bot_id))?.id||'');}, [data, active]);
  useEffect(() => {if (conversation?.bot_id) setTarget(conversation.bot_id); else if (conversation && !conversation.bot_ids.includes(target)) setTarget(conversation.bot_ids[0]); setManager(false);setThread(attentionNavigation.current?.conversation?.id===active?attentionNavigation.current.thread:null);attentionNavigation.current=null;setAttachments([]);setLibraryFiles([]);setMentions([]);setStandingGrant('');setLifecycleAuthority(false);}, [active]);
  useLayoutEffect(()=>{
    const element=scroll.current;if(!element)return;
    const scope=`${active}:${thread?.id||''}`;
    if(scrollHistory.current.scope!==scope){scrollHistory.current={scope,last:0};followBottom.current=true;restoreAnchor.current=null;setUnseen(false);setAnnouncement('');}
    const last=entries.at(-1)?.position||0;
    if(restoreAnchor.current){
      const anchor=restoreAnchor.current;
      const node=element.querySelector<HTMLElement>(`[data-entry-id="${anchor.id}"]`);
      element.scrollTop=node?element.scrollTop+node.getBoundingClientRect().top-element.getBoundingClientRect().top-anchor.offset:anchor.top+element.scrollHeight-anchor.height;
      restoreAnchor.current=null;
    }else if(followBottom.current){element.scrollTop=element.scrollHeight;}
    else if(last>scrollHistory.current.last&&scrollHistory.current.last){setUnseen(true);setAnnouncement('New activity in this conversation.');}
    scrollHistory.current.last=last;
  },[active,thread?.id,chat.data]);
  useEffect(()=>{
    const content=scroll.current?.querySelector('.transcript');if(!content)return;
    const observer=new ResizeObserver(()=>{if(followBottom.current&&scroll.current)scroll.current.scrollTop=scroll.current.scrollHeight;});
    observer.observe(content);return()=>observer.disconnect();
  },[active,thread?.id,!!chat.data]);
  useEffect(() => {
    if (!authenticated || !data || source.current) return;
    const eventSource = new EventSource(`/api/v1/events?after=${data.event_cursor}`);
    source.current = eventSource;
    const refresh = () => {
      if (refreshTimer.current) return;
      refreshTimer.current = setTimeout(() => {query.invalidateQueries({queryKey: ['state']}); query.invalidateQueries({queryKey: ['timeline']});query.invalidateQueries({queryKey:['attention']});query.invalidateQueries({queryKey:['attention-counts']});query.invalidateQueries({queryKey:['attention-detail']}); refreshTimer.current = undefined;}, 250);
    };
    eventSource.addEventListener('update', event => {
      const row: Event = JSON.parse((event as MessageEvent).data);
      setActivity(items => [row, ...items.filter(item => item.id !== row.id)].slice(0, 20));
      refresh();
    });
    eventSource.onopen = refresh;
    return () => {eventSource.close(); source.current = null; if (refreshTimer.current) clearTimeout(refreshTimer.current); refreshTimer.current = undefined;};
  }, [authenticated, !!data, query]);

  useEffect(()=>{if(authenticated&&attentionInitial)setAttentionOpen(true);},[authenticated,attentionInitial]);
  async function navigateAttention(value:AttentionTarget){
    if(!value.conversation)throw new Error('This source no longer has a conversation.');
    const conversation=value.conversation as Conversation;
    await api(`/api/v1/conversations/${conversation.id}${value.thread?`?thread=${value.thread.id}`:''}`);
    setAttentionConversation(conversation);attentionNavigation.current=value;
    if(active===conversation.id){setThread(value.thread);attentionNavigation.current=null;}
    setActive(conversation.id);setSidebar(false);setContextOpen(true);setContextTab('work');
    if(value.task)setInspect(value.task);
  }
  async function login(event: FormEvent) {
    event.preventDefault(); setError('');
    try {await api('/api/v1/session', {method: 'POST', headers: {Authorization: `Bearer ${ownerToken}`}}); setOwnerToken(''); setAuthenticated(true);} catch (err) {setError((err as Error).message);}
  }

  async function action(work: () => Promise<unknown>) {
    setError('');
    try {await work(); await query.invalidateQueries({queryKey: ['state']}); await query.invalidateQueries({queryKey: ['timeline']});} catch (err) {setError((err as Error).message);}
  }

  async function send(event?: FormEvent) {
    event?.preventDefault();
    if (!draft.trim() || sending || !active || !bot || bot.status!=='active' || bot.desired_state!=='running') return;
    setSending(true); setError('');
    const submittedScope=chatScope.current.generation,submittedDraft=draft;
    const payload = {intent:'auto',bot_id: conversation?.bot_id || target, conversation_id: active, prompt: draft.trim(),
      ...(standingGrant?{grant_id:standingGrant}:manager ? {managed_bot_ids: managedTargets,...(lifecycleAuthority?{management_operations:['delegate','inspect','steer','cancel','reassign','create','configure','start','stop']}: {})} : {}),
      attachment_ids:attachments.map(a=>a.id),artifact_ids:libraryFiles.map(a=>a.id),reply_to_message_id:thread?.id||null,mention_bot_ids:mentions.filter(id=>draft.includes(`@${botById(id)?.name}`))};
    const fingerprint = JSON.stringify(payload);
    if (pendingSend.current?.fingerprint !== fingerprint) pendingSend.current = {fingerprint, key: requestKey()};
    try {
      await api('/api/v1/messages', {method: 'POST', headers: {'Idempotency-Key': pendingSend.current.key}, body: JSON.stringify(payload)});
      pendingSend.current = null;
      if(chatScope.current.generation===submittedScope){setDraft(current=>current===submittedDraft?'':current);setManager(false);setAttachments([]);setLibraryFiles([]);setMentions([]);setStandingGrant('');setLifecycleAuthority(false);}
      await query.invalidateQueries({queryKey: ['state']}); await query.invalidateQueries({queryKey: ['timeline']});
    } catch (err) {if(chatScope.current.generation===submittedScope)setError((err as Error).message);} finally {setSending(false);}
  }

  async function makeGroup(event: FormEvent) {
    event.preventDefault();
    await action(async () => {
      const group = await api<{id:string}>(editingGroup?`/api/v1/groups/${editingGroup.id}`:'/api/v1/groups', {method: editingGroup?'PUT':'POST', body: JSON.stringify({title: groupTitle, bot_ids: members})});
      // A new ID must be in the cached roster before the active-conversation
      // recovery effect runs; otherwise it can immediately select an older chat.
      await query.invalidateQueries({queryKey:['state']});
      setActive(group.id); setGroupOpen(false);setEditingGroup(null);
    });
  }

  async function removeGroup() {
    if (!editingGroup) return;
    const id = editingGroup.id;
    await action(async () => {
      await api(`/api/v1/groups/${id}`, {method: 'DELETE'});
      if (activeRef.current === id) setActive(data?.conversations.find(c => c.kind === 'dm' && availableBots.some(b => b.id === c.bot_id))?.id || '');
      setGroupOpen(false); setEditingGroup(null);
    });
  }

  function select(item: Conversation) {setActive(item.id); setSidebar(false); setDraft(''); setError('');}
  async function uploadFiles(files:FileList|null){if(!files||!active)return;const uploadConversation=active;if(attachments.length+files.length>10){setError('A task can have at most 10 attachments');return;}await action(async()=>{const added:Attachment[]=[];for(const file of Array.from(files)){if(file.size>10_000_000)throw new Error('Each attachment is limited to 10 MB');const content=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Unable to read attachment'));reader.readAsDataURL(file);});const extension=file.name.split('.').pop()?.toLowerCase();const type=({md:'text/markdown',csv:'text/csv',py:'text/x-python',json:'application/json',txt:'text/plain'} as Record<string,string>)[extension||'']||file.type;added.push(await mutate<Attachment>(`/api/v1/conversations/${uploadConversation}/attachments`,'POST',{name:file.name,content_type:type,content_base64:content}));}if(activeRef.current===uploadConversation)setAttachments(current=>[...current,...added]);});}
  async function earlier(){
    followBottom.current=false;
    try{await chat.earlier(()=>{
      const element=scroll.current;if(!element)return;
      const node=[...element.querySelectorAll<HTMLElement>('[data-entry-id]')].find(node=>node.getBoundingClientRect().bottom>element.getBoundingClientRect().top);
      if(node)restoreAnchor.current={id:node.dataset.entryId!,offset:node.getBoundingClientRect().top-element.getBoundingClientRect().top,height:element.scrollHeight,top:element.scrollTop};
    });}catch(err){if((err as Error).name!=='AbortError')setError((err as Error).message);restoreAnchor.current=null;}
  }
  function openThread(message:Message){setThread(message);setDraft('');}
  function openArtifact(artifact: Artifact) {setLibraryInitial(artifact.id);setLibraryOpen(true);}

  function responseState(task:Task){
    const stopped=settled(task);
    return <div className={`request-state ${task.error?'has-error':''}`} data-response-task-id={task.id} key={task.id}>
      <div className="request-state-line"><span>{botById(task.bot_id)?.name||'Teammate'} · {task.message_intent==='answer'&&task.status==='completed'?'Answer recorded':taskStatus(task)}</span><button onClick={()=>setInspect(task)}>Inspect response</button>{!stopped&&<button disabled={task.status==='cancelling'} onClick={()=>action(()=>api(`/api/v1/tasks/${task.id}/stop`,{method:'POST'}))}>Stop response</button>}</div>
      {task.error&&<p className="response-error">{task.error}</p>}
      {task.intake_status==='failed'&&!stopped&&<button className="secondary" onClick={()=>action(()=>api(`/api/v1/messages/${task.id}/retry-interpretation`,{method:'POST'}))}>Retry interpretation</button>}
    </div>;
  }

  function renderEntry(entry:TimelineEntry){
    const item=entry.message;
    const expired=(request:{expires_at:string})=>new Date(request.expires_at).getTime()<=Date.now();
    if(entry.question)return <><div className="timeline-context">{botById(entry.author||'')?.name||'Teammate'} asked · <time dateTime={entry.occurred_at} title={new Date(entry.occurred_at).toString()}>{new Date(entry.occurred_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</time></div><InputCard request={entry.question} actionable={!!entry.actionable&&!expired(entry.question)}/></>;
    if(entry.review){
      const review=entry.review;
      return entry.actionable&&!expired(review)?<ApprovalCard approval={review}/>:<section className="review-history" data-review-id={review.id}><strong>Action review · {review.status==='pending'?'closed':statusLabel(review.status)}</strong><p>{String(review.payload.summary||review.payload.description||review.payload.command||'Action review')}</p><small>{new Date(entry.occurred_at).toLocaleString()} · {(review as Approval&{decision?:string}).decision||'No action available'}</small></section>;
    }
    if(entry.legacy_answer!==undefined)return <article className="message from-user"><div className="message-content"><div className="message-byline"><strong>You</strong><span>Recorded question answer</span></div><div className="prose"><Markdown>{entry.legacy_answer}</Markdown></div></div></article>;
    const cause=entry.cause_message_id?entries.find(e=>e.message?.id===entry.cause_message_id)?.message:undefined;
    const origin=entry.origin==='coordination'?'Teammate coordination':entry.origin==='delegated'?'Delegated result':null;
    if(!item)return <article className="message streamed" data-attempt-id={entry.attempt_id}><Avatar bot={botById(entry.author||'')} size={28}/><div className="message-content"><div className="message-byline"><strong>{botById(entry.author||'')?.name||'Teammate'}</strong><span>{statusLabel(entry.status||'running')}</span></div>{origin&&<div className="timeline-context">{origin}</div>}{cause&&<div className="timeline-context">Reply to: {cause.content.slice(0,140)}</div>}<div className="prose"><Markdown>{entry.text||''}</Markdown></div></div></article>;
    return <article data-message-id={item.id} className={`message ${item.role==='user'?'from-user':''} ${origin?'coordination-message':''}`}>
      {item.role!=='user'&&<Avatar bot={botById(item.author)} size={28}/>}
      <div className="message-content"><div className="message-byline"><strong>{item.role==='user'?(item.source_id?.startsWith('routine-revision:')?'You · saved routine instructions':'You'):botById(item.author)?.name||item.author}</strong><time dateTime={item.created_at} title={new Date(item.created_at).toString()} tabIndex={0}>{new Date(item.created_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</time>{item.edited_at&&<small>edited</small>}</div>
        {origin&&<div className="timeline-context">{origin}</div>}
        {item.role==='assistant'&&entry.cause_message_id&&<div className="timeline-context">Reply to: {cause?.content.slice(0,140)||`earlier message #${entry.cause_message_id}`}</div>}
        <div className="prose"><Markdown>{item.content}</Markdown></div>
        {!!item.mentions?.length&&<div className="attachment-chips">{item.mentions.map(id=><span key={id}>@{botById(id)?.name||id}</span>)}</div>}
        {!!item.attachment_ids?.length&&<div className="attachment-chips">{item.attachment_ids.map(id=><a key={id} href={`/api/v1/attachments/${id}`}>{chat.data?.attachments.find(a=>a.id===id)?.name||'Attachment'}</a>)}</div>}
        <div className="message-actions"><button onClick={()=>openThread(item)}>Reply{item.reply_count?` (${item.reply_count})`:''}</button><button aria-label={`React to message ${item.id}`} onClick={()=>action(()=>api(`/api/v1/messages/${item.id}/reactions`,{method:'POST',body:JSON.stringify({emoji:'👍'})}))}>👍</button>{item.reactions?.map(reaction=><button key={reaction.emoji} className="reaction-chip" onClick={()=>action(()=>api(`/api/v1/messages/${item.id}/reactions`,{method:'POST',body:JSON.stringify({emoji:reaction.emoji})}))}>{reaction.emoji} 1</button>)}{/* taste-skill-ignore: actual message reactions */}</div>
        {entry.tasks.map(responseState)}
      </div>
    </article>;
  }

  function ApprovalCard({approval,onError=setError}: {approval: Approval;onError?:(message:string)=>void}) {
    const reviewAction=(work:()=>Promise<unknown>)=>{onError('');return action(async()=>{try{return await work();}catch(err){onError((err as Error).message);throw err;}});};
    const summary=String(approval.payload.summary||approval.payload.description||approval.payload.command||'Review this action');
    const proposal=data?.proposals.find(p=>p.approval_id===approval.id);
    const exact=approval.payload.action as Record<string,unknown>|undefined;
    const secure=approval.kind==='input'&&exact?.input_type==='secure';
    const recipient=data?.work_items.flatMap(item=>item.outcomes||[]).find(outcome=>outcome.id===exact?.outcome_id)?.target.display_name;
    return <section className="review-card"><div className="review-title"><ShieldCheck size={18}/><strong>{approval.kind==='lifecycle'?'Review teammate changes':approval.kind==='skill'||approval.kind==='memory'?'Review proposed learning':secure?'Secure input requested':'Your review is needed'}</strong></div><p>{summary}</p>{approval.kind==='connector'&&exact?<ConnectorPreview action={exact} recipient={recipient}/>:<pre>{JSON.stringify(exact||approval.payload,null,2)}</pre>}<small>Expires {new Date(approval.expires_at).toLocaleString()} · approval {approval.id.slice(0,8)}</small>{approval.kind==='lifecycle'&&<pre>{JSON.stringify({configuration:approval.payload.configuration,before:approval.payload.before,resources:approval.payload.resources,affected_tasks:approval.payload.affected_task_ids},null,2)}</pre>}{secure&&<p>Type sensitive input directly into the computer, then hand back control before acknowledging completion.</p>}<div className="button-row">{secure&&<button className="secondary" onClick={()=>{setComputerBotId(String(exact?.bot_id));setComputerOpen(true);}}>Open computer for secure input</button>}<button className="primary" onClick={()=>reviewAction(()=>api(`/api/v1/approvals/${approval.id}`,{method:'POST',body:JSON.stringify({decision:'once',expected_payload_hash:approval.payload_hash})}))}><Check size={15}/>{secure?'Input completed · continue':'Allow once'}</button><button className="secondary" onClick={()=>reviewAction(()=>api(`/api/v1/approvals/${approval.id}`,{method:'POST',body:JSON.stringify({decision:'deny',expected_payload_hash:approval.payload_hash})}))}>Deny</button><button className="secondary" onClick={()=>reviewAction(()=>api(`/api/v1/approvals/${approval.id}`,{method:'POST',body:JSON.stringify({decision:'cancel',expected_payload_hash:approval.payload_hash})}))}>Cancel review</button>{!['runtime','lifecycle'].includes(approval.kind)&&<button className="secondary" onClick={()=>{const value={...exact};delete value.expected_hash;delete value.expected_revision;setReviewEdit({id:approval.id,hash:approval.payload_hash,summary,value:JSON.stringify(value,null,2)});}}>Edit draft</button>}{proposal&&<button className="secondary" onClick={()=>setProposalEdit({id:proposal.id,value:JSON.stringify({action:proposal.action,bot_id:proposal.action==='create'?null:proposal.bot_id,summary:proposal.payload.summary,configuration:proposal.payload.configuration,interrupt:proposal.payload.interrupt},null,2)})}>Edit proposal</button>}</div></section>;
  }

  if (authenticated === null) return <div className="login-shell"><p>Opening your workspace…</p></div>;
  if (!authenticated) return <div className="login-shell"><form className="login-card" onSubmit={login}><span className="wordmark">hermbot<span>✳</span>{/* taste-skill-ignore: established Hermbot mark */}</span><h1>Your team, your workspace.</h1><p>Enter the owner token from your local <code>.env</code> file to open this private workspace.</p><label htmlFor="owner-token">Owner token</label><input id="owner-token" type="password" autoComplete="off" value={ownerToken} onChange={event => setOwnerToken(event.target.value)} required/><button className="primary" type="submit">Open workspace <ArrowUpRight size={18}/></button>{error && <p role="alert" className="error-text">{error}</p>}<small>Runs continue here when you close the browser.</small></form></div>;

  return <div className="workspace">
    {sidebar && <button className="sidebar-scrim" aria-label="Close navigation" onClick={() => setSidebar(false)}/>}
    <aside className={`sidebar ${sidebar ? 'open' : ''}`}>
      <div className="brand-row"><span className="wordmark">hermbot<span>✳</span>{/* taste-skill-ignore: established Hermbot mark */}</span><div className="button-row"><button className="icon" aria-label="Create teammate" onClick={()=>{setBotEditor({});setSidebar(false);}}><Users size={18}/></button><button className="icon" aria-label="New group conversation" onClick={() => {setEditingGroup(null);setGroupTitle('Project chat');setMembers(availableBots.slice(0,3).map(b=>b.id));setGroupOpen(true);setSidebar(false);}}><Plus size={19}/></button></div></div>
      <div className="search"><Search size={15}/><input placeholder="Find a teammate…" aria-label="Find a teammate or group" value={filter} onChange={event => setFilter(event.target.value)}/></div>
      <div className="sidebar-scroll">
      <div className="section-label"><button className="sidebar-section-toggle" aria-label="Your teammates" aria-expanded={sectionOpen('team')} aria-controls="team-conversations" onClick={()=>toggleSection('team')}>{sectionOpen('team')?<ChevronDown size={12}/>:<ChevronRight size={12}/>}Your teammates <span>{availableBots.length}</span></button></div>
      <nav id="team-conversations" aria-label="Team conversations" hidden={!sectionOpen('team')}>{data?.conversations.filter(item => item.kind === 'dm' && availableBots.some(b=>b.id===item.bot_id) && (item.title.toLowerCase().includes(filter.toLowerCase()) || botById(item.bot_id!)?.role.toLowerCase().includes(filter.toLowerCase()))).map(item => {
        const teammate = botById(item.bot_id!); const summary = executionSummary(data.tasks.filter(task=>task.bot_id===item.bot_id)); const busy=summary.task;
        return <button key={item.id} className={`roster-row ${active === item.id ? 'selected' : ''}`} onClick={() => select(item)}><Avatar bot={teammate}/><span><strong>{teammate?.name}</strong><small>{busy ? summary.label : teammate?.status!=='active'?teammate?.status:teammate?.desired_state==='stopped'?'Runtime stopped':teammate?.role.split(',')[0]}</small></span><i className={`presence ${summary.executing ? 'working' : ''}`}/></button>;
      })}</nav>
      {!!pastConversations.length&&<><div className="section-label groups-label"><button className="sidebar-section-toggle" aria-label="Past teammates" aria-expanded={sectionOpen('past')} aria-controls="past-conversations" onClick={()=>toggleSection('past')}>{sectionOpen('past')?<ChevronDown size={12}/>:<ChevronRight size={12}/>}Past teammates <span>{pastConversations.length}</span></button></div><nav id="past-conversations" aria-label="Archived conversations" hidden={!sectionOpen('past')}>{pastConversations.filter(c=>c.title.toLowerCase().includes(filter.toLowerCase())).map(item=><button key={item.id} className={`roster-row ${active===item.id?'selected':''}`} onClick={()=>select(item)}><Avatar bot={botById(item.bot_id!)}/><span><strong>{item.title}</strong><small>{botById(item.bot_id!)?.status==='archived'?'Archived':'Deleted · saved conversation'}</small></span></button>)}</nav></>}
      <div className="section-label groups-label"><button className="sidebar-section-toggle" aria-label="Groups" aria-expanded={sectionOpen('groups')} aria-controls="group-conversations" onClick={()=>toggleSection('groups')}>{sectionOpen('groups')?<ChevronDown size={12}/>:<ChevronRight size={12}/>}Groups <span>{data?.conversations.filter(c=>c.kind==='group').length||0}</span></button><button className="icon" aria-label="Create group" onClick={() => {setEditingGroup(null);setGroupTitle('Project chat');setMembers(availableBots.slice(0,3).map(b=>b.id));setGroupOpen(true);setSidebar(false);}}><Plus size={15}/></button></div>
      <nav id="group-conversations" aria-label="Group conversations" hidden={!sectionOpen('groups')}>{data?.conversations.filter(item => item.kind === 'group' && item.title.toLowerCase().includes(filter.toLowerCase())).map(item => <button key={item.id} className={`roster-row group-row ${active === item.id ? 'selected' : ''}`} onClick={() => select(item)}><span className="group-avatar"><Users size={18}/></span><span><strong>{item.title}</strong><small>{item.bot_ids.length} teammates</small></span></button>)}</nav>
      {!data?.conversations.some(item => item.kind === 'group') && <button className="create-group" onClick={() => {setEditingGroup(null);setGroupTitle('Project chat');setMembers(availableBots.slice(0,3).map(b=>b.id));setGroupOpen(true);setSidebar(false);}}><Plus size={14}/>Bring the team together</button>}
      </div>
      <div className="sidebar-bottom"><button className="roster-row routines-nav attention-nav" aria-label={`Attention, ${attentionCounts.data?.unread||0} unread, ${attentionCounts.data?.needs_owner||0} need you`} onClick={()=>{setAttentionInitial(null);setAttentionOpen(true);setSidebar(false);}}><Circle size={18}/><strong>Attention</strong><span className="attention-badge">{attentionCounts.data?.unread||0} unread · {attentionCounts.data?.needs_owner||0} need you</span></button><button className="roster-row routines-nav" onClick={()=>{setRoutinesOpen(true);setSidebar(false);}}><Clock3 size={18}/><strong>Routines</strong></button><details className="owner-menu" ref={ownerMenu}><summary className="owner-row" aria-label="Owner menu"><span className="owner-avatar">D</span><span className="owner-name"><strong>Owner</strong><small>Local workspace</small></span><ChevronDown size={16}/></summary><div className="owner-popover"><button onClick={()=>{ownerMenu.current!.open=false;setConnectionsOpen(true);setSidebar(false);}}><Users size={17}/>Connections</button><button onClick={()=>{ownerMenu.current!.open=false;setLibraryInitial(null);setLibraryOpen(true);setSidebar(false);}}><FileText size={17}/>File library</button><button onClick={()=>{ownerMenu.current!.open=false;setSettingsOpen(true);setSidebar(false);}}><Layers3 size={17}/>Settings</button><button onClick={()=>{ownerMenu.current!.open=false;setTheme(theme==='light'?'dark':'light');}}>{theme==='light'?<Moon size={17}/>:<Sun size={17}/>}Switch to {theme==='light'?'dark':'light'} theme</button><button onClick={async()=>{ownerMenu.current!.open=false;await api('/api/v1/session', {method: 'DELETE'});await clearBrowserPush();setAttentionOpen(false);source.current?.close();source.current=null;query.clear();clearMutationKeys();setThread(null);setActive('');setAuthenticated(false);}}><LogOut size={17}/>Sign out</button></div></details></div>
    </aside>
    <main className="main-panel">
      <header className="chat-header">
        <button className="icon mobile-menu" aria-label="Open navigation" onClick={() => {setSidebar(true);if(window.matchMedia('(max-width: 950px)').matches)setContextOpen(false);}}><Menu size={20}/></button>
        {conversation?.kind === 'group' ? <span className="group-avatar"><Users size={20}/></span> : <Avatar bot={bot}/>}
        {conversation?.kind==='group'&&<button className="icon header-direct-tool" aria-label="Edit group" onClick={()=>{setEditingGroup(conversation);setGroupTitle(conversation.title);setMembers(conversation.bot_ids);setGroupOpen(true);}}><Users size={16}/></button>}
        <span className="header-title"><h1 title={conversation?.title}>{conversation?.title || 'Your workspace'}</h1><p>{conversation?.kind === 'group' ? conversation.bot_ids.map(id => botById(id)?.name).join(' · ') : bot?.role}</p></span>
        {bot&&bot.status!=='deleted'&&<button className="icon header-direct-tool" aria-label={`Edit ${bot.name}`} onClick={()=>setBotEditor({bot})}><Users size={17}/></button>}
        {bot&&bot.status!=='deleted'&&<><button className="icon header-direct-tool" aria-label={`Open ${bot.name} memory and skills`} onClick={()=>{setWorkflowTask(null);setKnowledgeOpen(true);}} title="Memory & skills"><BookOpen size={17}/></button><button className="icon header-direct-tool" aria-label={`Open ${bot.name} computer`} onClick={()=>{setComputerBotId(bot.id);setComputerOpen(true);}} title="Bot computer"><Monitor size={17}/></button></>}
        <span className="header-presence"><i className={`presence ${ongoing.length ? 'working' : ''}`}/>{ongoing.length ? executionSummary(ongoing).label : bot?.desired_state==='stopped'?'Stopped':bot?.status!=='active'?bot?.status:bot?.readiness.compatible ? 'Ready' : 'Connecting'}</span>
        {((bot&&bot.status!=='deleted')||conversation?.kind==='group')&&<details className="header-tools" key={active}><summary aria-label="Conversation tools" title="Conversation tools"><Ellipsis size={20}/></summary><div className="header-tools-menu">
          {conversation?.kind==='group'&&<button onClick={event=>{event.currentTarget.closest('details')?.removeAttribute('open');setEditingGroup(conversation);setGroupTitle(conversation.title);setMembers(conversation.bot_ids);setGroupOpen(true);}}>Edit group</button>}
          {bot&&bot.status!=='deleted'&&<><button onClick={event=>{event.currentTarget.closest('details')?.removeAttribute('open');setBotEditor({bot});}}>Edit {bot.name}</button><button onClick={event=>{event.currentTarget.closest('details')?.removeAttribute('open');setWorkflowTask(null);setKnowledgeOpen(true);}}>Memory and skills</button><button onClick={event=>{event.currentTarget.closest('details')?.removeAttribute('open');setComputerBotId(bot.id);setComputerOpen(true);}}>Computer</button></>}
        </div></details>}
        <button className={`icon context-toggle ${contextOpen ? 'pressed' : ''}`} aria-label="Toggle work panel" aria-pressed={contextOpen} aria-expanded={contextOpen} aria-controls="work-panel" title="Work and outputs" onClick={() => {setContextOpen(!contextOpen);setSidebar(false);}}><Layers3 size={19}/></button>
      </header>
      {data?.protocol_fixture && <div className="setup-note"><Circle size={13}/><span>Protocol test mode. Responses come from a deterministic test fixture. Connect your model for live work.</span></div>}
      {!data?.provider_configured && <div className="setup-note"><Circle size={13}/><span>Connect a model to start your team. Set the provider URL, model, and API key in <code>.env</code>, then restart the services.</span></div>}
      {error && <div role="alert" className="error-banner">{error}<button className="icon" aria-label="Dismiss error" onClick={() => setError('')}><X size={15}/></button></div>}
      {state.error && <div role="alert" className="error-banner">Workspace connection lost. Your tasks remain on the server.</div>}
      <div className="conversation-scroll" ref={scroll} onScroll={event=>{const node=event.currentTarget;followBottom.current=node.scrollHeight-node.scrollTop-node.clientHeight<80;if(followBottom.current){setUnseen(false);setAnnouncement('');}}}>
        {chat.isPending?<p className="conversation-notice" role="status">Loading conversation…</p>:chat.error&&!chat.data?<div className="conversation-notice" role="alert"><p>Could not load this conversation.</p><button className="secondary" onClick={()=>chat.refetch()}>Retry loading conversation</button></div>:!entries.length ? <div className="welcome"><Avatar bot={bot} size={66}/><h2>What should {conversation?.kind === 'group' ? 'your team' : bot?.name || 'we'} work on?</h2><p>Give a teammate an outcome. They can work with the rest of your team and bring the result back here.</p><div className="starter-grid"><button onClick={() => setDraft('Research the material I provide, identify the key findings, and ask Quill to help turn them into a clear report.')}><Search size={18}/><strong>Research & write</strong><span>Turn evidence into a useful report</span><ArrowUpRight size={16}/></button><button onClick={() => setDraft('Analyze the CSV I provide, check the calculations with a teammate, and return the findings as an artifact.')}><FileText size={18}/><strong>Analyze & check</strong><span>Work through data together</span><ArrowUpRight size={16}/></button><button onClick={() => {setManager(true);setManagedTargets(availableBots.filter(b=>b.id!==bot?.id).slice(0,9).map(b=>b.id)); setDraft('Coordinate your permitted teammates to produce a report. Assign the research first, then writing based on the research, review the work, and bring me the final artifact.');}}><Users size={18}/><strong>Coordinate the team</strong><span>Assign work and combine the results</span><ArrowUpRight size={16}/></button></div></div> : <div className="transcript">
          {chat.data?.has_earlier&&<button className="secondary" disabled={chat.loadingEarlier} onClick={earlier}>{chat.loadingEarlier?'Loading earlier messages…':'Load earlier messages'}</button>}
          <div role="log" aria-label="Conversation history" aria-live="off">
            {entries.map((entry,index)=><Fragment key={entry.id}>
              {(index===0||new Date(entry.occurred_at).toDateString()!==new Date(entries[index-1].occurred_at).toDateString())&&<div className="conversation-day"><time dateTime={entry.occurred_at}>{new Date(entry.occurred_at).toLocaleDateString([],{weekday:'short',year:'numeric',month:'short',day:'numeric'})}</time></div>}
              <div data-entry-id={entry.id}>{renderEntry(entry)}</div>
            </Fragment>)}
          </div>
        </div>}
        {chat.error&&chat.data&&<div className="conversation-notice" role="status">Conversation refresh failed. Showing saved history. <button onClick={()=>chat.refetch()}>Retry</button></div>}
        {tasks.some(t=>['uncertain','unresolved'].includes(t.connector_state||''))&&<div className="conversation-notice" role="alert"><p>An application outcome needs review before this work can continue.</p><button className="secondary" onClick={()=>{setConnectionsTab('Activity');setConnectionsOpen(true);}}>Review application outcome</button></div>}
        {backgroundWork.length > 0 && <div className="work-presence"><span className="working-dots"><i/><i/><i/></span>{backgroundWork.length === 1 ? `${botById(backgroundWork[0].bot_id)?.name} · ${taskStatus(backgroundWork[0])}` : `${backgroundWork.length} tasks in progress`}</div>}
        <div ref={bottom}/>
      </div>
      <div className="timeline-announcement" role="status" aria-live="polite">{announcement}</div>
      <div className="composer-area">
        {unseen&&<button className="secondary new-messages" onClick={()=>{followBottom.current=true;bottom.current?.scrollIntoView({block:'end'});setUnseen(false);setAnnouncement('');}}>New messages ↓</button>}
        {thread&&<div className="thread-banner"><span>Thread · {thread.content.slice(0,90)}</span><button aria-label="Close thread" onClick={()=>{setThread(null);}}>×</button></div>}
        <form className="composer" onSubmit={send}>
          {!!attachments.length&&<div className="attachment-chips">{attachments.map(asset=><button type="button" key={asset.id} onClick={()=>setAttachments(current=>current.filter(a=>a.id!==asset.id))}>{asset.name} ×</button>)}</div>}
          {!!libraryFiles.length&&<div className="attachment-chips">{libraryFiles.map(file=><button type="button" key={file.id} onClick={()=>setLibraryFiles(current=>current.filter(f=>f.id!==file.id))}>{file.name} · v{file.version} ×</button>)}</div>}
          <textarea aria-label="Message your teammate" placeholder={`Message ${conversation?.kind === 'group' ? 'your team' : bot?.name || 'a teammate'}…`} value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => {if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {event.preventDefault(); send();}}} rows={2}/>
          <div className="composer-bottom"><div className="composer-options">
            {conversation?.kind === 'group' && <label className="target-picker">To<select aria-label="First recipient" value={target} onChange={event => {setTarget(event.target.value);setStandingGrant('');setManager(false);setManagedTargets([]);}}>{conversation.bot_ids.filter(id=>availableBots.some(b=>b.id===id)).map(id => <option key={id} value={id}>{botById(id)?.name}</option>)}</select><ChevronDown size={12}/></label>}
            <label className={`manager-switch ${manager ? 'active' : ''}`}><input type="checkbox" checked={manager} onChange={event => {setManager(event.target.checked);setStandingGrant('');if(event.target.checked)setManagedTargets(availableBots.filter(b=>b.id!==bot?.id&&(!conversation||conversation.kind==='dm'||conversation.bot_ids.includes(b.id))).slice(0,9).map(b=>b.id));}}/><Users size={15}/>Coordinate team</label>
            <label className="icon upload-control" title="Attach files"><FileText size={16}/><input aria-label="Attach files" type="file" multiple onChange={event=>{uploadFiles(event.target.files);event.target.value='';}}/></label>
            <button className="icon" type="button" aria-label="Create a routine from this message" disabled={!draft.trim() || manager || !!standingGrant || !!thread || (attachments.length>0||libraryFiles.length>0) || !data?.provider_configured} onClick={() => {setRoutinesOpen(true);}}><Clock3 size={16}/></button>
          </div><button type="submit" className="send-button" aria-label="Send message" disabled={!draft.trim() || sending || !bot || bot.status!=='active' || bot.desired_state!=='running'}><ArrowUp size={18}/></button></div>
          <div className="composer-extra">
            {conversation&&<select aria-label="Mention teammate" value="" onChange={event=>{const peer=botById(event.target.value);if(peer){setDraft(current=>current+` @${peer.name} `);setMentions(current=>Array.from(new Set([...current,peer.id])));}}}><option value="">@ Mention</option>{conversation.bot_ids.map(id=><option key={id} value={id}>{botById(id)?.name||id}</option>)}</select>}
            {!!data?.grants.some(g=>g.kind==='standing'&&g.manager_id===bot?.id&&!g.revoked_at&&new Date(g.expires_at)>new Date())&&<select aria-label="Management grant" value={standingGrant} onChange={event=>{setStandingGrant(event.target.value);setManager(false);}}><option value="">No standing grant</option>{data.grants.filter(g=>g.kind==='standing'&&g.manager_id===bot?.id&&!g.revoked_at&&new Date(g.expires_at)>new Date()).map(g=><option key={g.id} value={g.id}>Manage {g.targets.map(id=>botById(id)?.name||id).join(', ')||'new teammates'}</option>)}</select>}
            {manager&&<details className="authority-picker"><summary>Manage {managedTargets.map(id=>botById(id)?.name||id).join(', ')||'selected teammates'}</summary>{availableBots.filter(b=>b.id!==bot?.id).map(peer=><label className="check-row" key={peer.id}><input type="checkbox" checked={managedTargets.includes(peer.id)} onChange={event=>setManagedTargets(current=>event.target.checked?[...current,peer.id].slice(0,9):current.filter(id=>id!==peer.id))}/>{peer.name}</label>)}<label className="check-row"><input type="checkbox" checked={lifecycleAuthority} onChange={event=>setLifecycleAuthority(event.target.checked)}/>Allow lifecycle proposals for owner review</label></details>}
          </div>
        </form><div className="composer-caption"><span>{manager ? 'This request grants coordination over your other teammates.' : bot?.status==='deleted'?'This teammate was deleted. Its saved conversation and outputs remain available.':bot?.status==='archived'?'Restore this teammate in settings to assign work.':bot?.desired_state==='stopped'?'Start this teammate in settings to assign work.':'Your team works in the background. You stay in control.'}</span><span>↵ send · ⇧↵ new line</span></div>
      </div>
    </main>
    {contextOpen&&<button className="context-scrim" aria-label="Close work panel backdrop" onClick={()=>setContextOpen(false)}/>}
    {contextOpen && <aside className="context-panel" id="work-panel" aria-label="Work and outputs"><div className="context-header"><h2>In this conversation</h2><button className="icon mobile-close" aria-label="Close work panel" onClick={() => setContextOpen(false)}><X size={18}/></button></div><div className="context-tabs"><button className={contextTab === 'work' ? 'active' : ''} onClick={() => setContextTab('work')}>Work <span>{visibleWork.length}</span></button><button className={contextTab === 'outputs' ? 'active' : ''} onClick={() => setContextTab('outputs')}>Outputs <span>{outputs.length}</span></button></div><div className="context-scroll">{contextTab === 'work' ? <>
      <WorkItems openOutput={openArtifact} items={workItems} botById={botById} grants={data?.grants||[]} action={action} inspect={setInspect} saveSkill={task=>{setWorkflowTask(task.id);setKnowledgeOpen(true);}} edit={task=>{setEditTask(task);setEditPrompt(task.input);}} redirect={task=>{setSteering(task);setDirection('');}} reviewOutcome={()=>{setConnectionsTab('Activity');setConnectionsOpen(true);}}/>
      {activity.some(item => item.conversation_id === active) && <div className="activity"><div className="section-label">Recent activity</div>{activity.filter(item => item.conversation_id === active && !['message.delta', 'tool.receipt'].includes(item.kind)).slice(0, 6).map(item => <div key={item.id}><i/><span>{String(item.data.tool || item.data.summary || item.data.name || item.kind.replaceAll('.', ' '))}</span></div>)}</div>}
    </> : <>{!outputs.length && <div className="context-empty"><FileText size={26}/><h3>Results you can keep.</h3><p>Files and reports your teammates produce appear here, linked to the work that created them.</p></div>}{outputs.map(artifact => <button className="artifact-card" key={artifact.id} onClick={() => openArtifact(artifact)}><FileText size={22}/><span><strong>{artifact.name}</strong><small>{botById(artifact.bot_id)?.name} · {formatFileSize(artifact.size)}</small></span><ArrowUpRight size={15}/></button>)}</>}</div><div className="context-footer"><ShieldCheck size={13}/>Each teammate has its own private workspace.</div></aside>}
    <Modal open={groupOpen} onOpenChange={value=>{setGroupOpen(value);if(!value)setEditingGroup(null);}} title={editingGroup?'Edit group':'Bring your team together'} description="Create a conversation for a shared outcome. Each teammate keeps its own workspace."><form onSubmit={makeGroup}><label htmlFor="group-title">Conversation name</label><input id="group-title" value={groupTitle} onChange={event => setGroupTitle(event.target.value)} required maxLength={120}/><div className="member-list">{availableBots.map(item => <label key={item.id}><input type="checkbox" checked={members.includes(item.id)} onChange={event => setMembers(event.target.checked ? [...members, item.id] : members.filter(id => id !== item.id))}/><Avatar bot={item} size={28}/><span>{item.name}<small>{item.role}</small></span></label>)}</div><button className="primary" disabled={members.length < 2||members.length>6}>{editingGroup?'Save group changes':'Create conversation'}</button>{editingGroup&&<><p className="form-note">Remove this group from the sidebar. Saved work and files remain available. Stop pending work first.</p>{error&&<p role="alert" className="error-text">{error}</p>}<button type="button" className="danger-text" onClick={removeGroup}>Remove group</button></>}</form></Modal>
    <Attention onArtifact={openArtifact} open={attentionOpen} initial={attentionInitial} onClose={()=>{setAttentionOpen(false);setAttentionInitial(null);const url=new URL(location.href);url.searchParams.delete('attention');history.replaceState(null,'',url);}} onNavigate={navigateAttention} onSettings={(section,id,kind)=>{setAttentionSettings(id&&kind?{id,kind}:null);if(section==='connections'){setConnectionsTab('Connections');setConnectionsOpen(true);}else setRoutinesOpen(true);}} renderReview={(approval,onError)=><ApprovalCard approval={approval} onError={onError}/>}/>
    <Routines attentionTarget={attentionSettings} open={routinesOpen} onClose={()=>{setRoutinesOpen(false);setAttentionSettings(null);}} state={data} conversationId={active} initialInstructions={draft} onConnections={()=>{setRoutinesOpen(false);setConnectionsOpen(true);}} onNavigate={async(id,threadId)=>{setActive(id);setThread(null);if(threadId){try{const result=await api<Chat>(`/api/v1/conversations/${id}?thread=${threadId}`);const message=result.thread_root||result.messages.find(m=>m.id===threadId);if(message)setThread(message);}catch(err){setError((err as Error).message);}}}}/>
    <Knowledge open={knowledgeOpen} onClose={()=>setKnowledgeOpen(false)} bot={bot} tasks={data?.tasks||[]} initialTask={workflowTask}/>
    <Computer open={computerOpen} onClose={()=>setComputerOpen(false)} bot={botById(computerBotId||bot?.id||'')}/>
    <Connections initialIdentity={attentionSettings?.kind==='connection'?attentionSettings.id:null} initialTab={connectionsTab} open={connectionsOpen} onClose={()=>{setConnectionsOpen(false);setAttentionSettings(null);}} bots={data?.bots||[]}/>
    <Library open={libraryOpen} onClose={()=>setLibraryOpen(false)} conversations={data?.conversations||[]} conversationId={active} initialFile={libraryInitial} onUse={file=>setLibraryFiles(current=>current.some(f=>f.id===file.id)?current:[...current,file].slice(0,10))} onNavigate={id=>{setActive(id);setThread(null);}}/>
    <Modal open={!!steering} onOpenChange={open => {if (!open) setSteering(null);}} title="Redirect the work" description="Your direction stays with this task, including when it is waiting for a teammate."><form onSubmit={event => {event.preventDefault(); action(async () => {await mutate(`/api/v1/tasks/${steering?.id}/steer`,'POST',{prompt:direction}); setSteering(null);});}}><label htmlFor="direction">What should change?</label><textarea id="direction" value={direction} onChange={event => setDirection(event.target.value)} required rows={4}/><button className="primary">Send direction</button></form></Modal>
    <SettingsWindow open={settingsOpen} onClose={()=>setSettingsOpen(false)} state={data} onEdit={item=>{setSettingsOpen(false);setBotEditor({bot:item});}} onClone={item=>{setSettingsOpen(false);setBotEditor({bot:item,clone:true});}} onLifecycle={(item,actionName)=>{setSettingsOpen(false);setLifecycle({bot:item,action:actionName});}}/>
    <BotEditor open={!!botEditor} onClose={()=>setBotEditor(null)} bot={botEditor?.bot} clone={botEditor?.clone} onSaved={async id=>{const updated=await api<State>('/api/v1/state');const dm=updated.conversations.find(c=>c.bot_id===id);if(dm)setActive(dm.id);}}/>
    {lifecycle&&<LifecycleDialog bot={lifecycle.bot} action={lifecycle.action} onClose={()=>setLifecycle(null)}/>}
    <Panel open={!!editTask} onClose={()=>setEditTask(null)} title="Edit queued instructions" description="These instructions can be edited until execution starts."><form className="settings-form" onSubmit={event=>{event.preventDefault();action(async()=>{await api(`/api/v1/tasks/${editTask?.id}`,{method:'PUT',body:JSON.stringify({prompt:editPrompt,expected_input_hash:editTask?.input_hash})});setEditTask(null);});}}><label>Instructions<textarea aria-label="Queued instructions" value={editPrompt} onChange={event=>setEditPrompt(event.target.value)} rows={6} required/></label><button className="primary">Save instructions</button></form></Panel>
    <Panel open={!!reviewEdit} onClose={()=>setReviewEdit(null)} title="Edit review draft" description="Saving creates a new approval. The previous approval cannot authorize this edited action."><form className="settings-form" onSubmit={e=>{e.preventDefault();action(async()=>{await mutate(`/api/v1/approvals/${reviewEdit!.id}`,'PUT',{expected_payload_hash:reviewEdit!.hash,summary:reviewEdit!.summary,payload:JSON.parse(reviewEdit!.value)});setReviewEdit(null);});}}><label>Summary<input value={reviewEdit?.summary||''} onChange={e=>setReviewEdit(v=>v?{...v,summary:e.target.value}:null)}/></label><ConnectorDraftFields value={reviewEdit?.value||''} onChange={value=>setReviewEdit(v=>v?{...v,value}:null)}/><button className="primary">Replace review</button></form></Panel>
    <Panel open={!!proposalEdit} onClose={()=>setProposalEdit(null)} title="Edit lifecycle proposal" description="Editing replaces this review. Approval of the old payload becomes invalid."><form className="settings-form" onSubmit={event=>{event.preventDefault();action(async()=>{await mutate(`/api/v1/proposals/${proposalEdit?.id}`,'PUT',JSON.parse(proposalEdit!.value));setProposalEdit(null);});}}><label>Exact proposed changes<textarea aria-label="Proposed changes" value={proposalEdit?.value||''} onChange={event=>setProposalEdit(current=>current?{...current,value:event.target.value}:null)} rows={14}/></label><button className="primary">Replace proposal</button></form></Panel>
    {inspect&&<TaskInspector task={inspect} data={data} onClose={()=>setInspect(null)}/>}
  </div>;
}

function TaskInspector({task,data,onClose}:{task:Task;data?:State;onClose:()=>void}) {
  const detail=useQuery({queryKey:['task',task.id],queryFn:()=>api<Task&{children:Task[];executions:{number:number;status:string}[];events:Event[]}>(`/api/v1/tasks/${task.id}`),refetchInterval:5000});
  const grant=data?.grants.find(g=>g.id===task.grant_id);
  const scope=detail.data?.management_scope;
  const tree=Array.from(new Map([...(data?.tasks.filter(item=>item.root_id===task.root_id)||[]),...(detail.data?.children||[]),detail.data||task].map(item=>[item.id,item])).values()).sort((a,b)=>a.depth-b.depth);
  return <Panel open onClose={onClose} title="Inspect the work" description="Assignments, execution progress and decisions for this operation."><div className="settings-list"><p className="prose">{task.input}</p>{tree.map(item=><div className="settings-item" key={item.id} style={{marginLeft:item.depth*12}}><span><strong>{data?.bots.find(b=>b.id===item.bot_id)?.name||item.bot_id}</strong><small>{taskStatus(item)} · {item.input.slice(0,100)}</small>{item.error&&<p className="error-text">{item.error}</p>}<TaskPreparation task={detail.data?.id===item.id?detail.data:item}/><TaskActivity task={detail.data?.id===item.id?detail.data:item}/>{item.output&&<p>{item.output.slice(0,500)}</p>}</span></div>)}{grant&&<div className="grant-card"><strong>Owner-issued authority</strong><p>{(scope?.targets||grant.targets).map(id=>data?.bots.find(b=>b.id===id)?.name||id).join(', ')}</p><small>{(scope?.operations||grant.operations).join(', ')} · expires {new Date(grant.expires_at).toLocaleString()}</small></div>}{detail.data?.stop_reason&&<p className="form-note">{detail.data.stop_reason}</p>}{detail.data?.execution_budget&&<p className="form-note">Active work: {Math.round(detail.data.execution_budget.used_seconds/60)} of {Math.round(detail.data.execution_budget.limit_seconds/60)} minutes</p>}{detail.data?.assessments?.map(assessment=><div className="grant-card" key={assessment.id}><strong>Execution assessment</strong><small>{(['queued','evaluating'].includes(assessment.status)?'Checking execution':assessment.recommendation||'Unavailable')} · {assessment.status}{assessment.confidence!=null?` · ${Math.round(assessment.confidence*100)}% confidence`:''}</small>{assessment.error&&<p className="form-note">{assessment.error}</p>}</div>)}{detail.data?.executions.map(run=><p className="form-note" key={run.number}>Execution {run.number}: {statusLabel(run.status)}</p>)}{data?.controls.filter(c=>c.task_id===task.id).map(control=><div className="grant-card" key={control.id}><p>{control.input}</p><small>Direction: {control.status==='included'?'Included in execution':control.status==='uncertain'?'Delivery needs reconciliation; not automatically repeated':control.status}</small></div>)}{data?.proposals.filter(p=>p.task_id===task.id).map(p=><p className="form-note" key={p.id}>Teammate proposal: {p.action} · {p.status}</p>)}{detail.data?.events.map(event=><p className="form-note" key={event.id}>{String(event.data.tool||event.kind.replaceAll('.',' '))} · {statusLabel(event.kind.split('.').slice(1).join(' '))}<small className="execution-event-time">{new Date(event.created_at).toLocaleTimeString()}</small></p>)}</div></Panel>;
}
