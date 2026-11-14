export type BotId = string;
export type RecurringManagementSettings = {grant_id:string;targets:BotId[];operations:string[]};
export type Bot = {id: BotId; name: string; role: string; color: string; avatar: string; capabilities: string[]; status: string; desired_state: string; revision: number; applied_revision: number; runtime_error: string | null; provider_id: string | null; model_name: string; enabled_tools: string[]; enabled_skills: string[]; memory_mb: number; cpus: number; readiness: {compatible?: boolean; error?: string}};
export type Conversation = {id: string; title: string; kind: 'dm' | 'group'; bot_id: BotId | null; bot_ids: BotId[]};
export type ExecutionPlan={version?:number;ready?:boolean;skills?:{name:string;sha256:string}[];blockers?:{code:string;name:string;message:string}[];handoff_blockers?:{code:string;name:string;message:string}[]};
export type HandoffContext={source_task_id:string;expected_output:string;artifact_ids:string[];attachment_ids:string[]};
export type ExecutionActivity = {waiting_model_requests?:number;started_at:string|null;last_contact_at:string|null;execution_status:string|null;last_progress_at:string|null;last_tool_event:string|null;last_tool_at:string|null;last_tool:string|null};
export type Task = {execution_kind?:string;owner_message_id?:number|null;reply_to_message_id?:number|null;management_scope?:{manager_id:string;targets:BotId[];operations:string[];observed_expires_at:string};activity?:ExecutionActivity|null;intake_status?:string;orchestration_version?:number;work_revision?:number;execution_plan?:ExecutionPlan;handoff_context?:HandoffContext;work_item_id?:string|null; message_intent?:string; connector_state?:string|null; id: string; root_id: string; parent_id: string | null; bot_id: BotId; conversation_id: string; input: string; input_hash: string; status: string; output: string | null; error: string | null; grant_id: string | null; depth: number; context: string; created_at: string; deadline: string | null; attempts: number; stop_kind?:string|null; stop_reason?:string|null; execution_budget?:{used_seconds:number;limit_seconds:number;remaining_seconds:number}; assessments?:{id:string;status:string;recommendation:string|null;confidence:number|null;error:string|null}[]};
export type Contribution = {task_id:string;bot_id:BotId;required:boolean;expected_output:string;status:string};
export type WorkOutcome = {owner_task_id?:string|null;contributions?:Contribution[];acceptance?:{evidence:{review:string};created_at:string}|null;id:string;key:string;title:string;kind:string;required:boolean;revision:number;status:string;reason:string;evidence_strength:string;target:{resource?:string;display_name?:string}};
export type WorkItem = {id:string; conversation_id:string; bot_id:BotId; title:string; objective:string; status:string; hidden_at:string|null; legacy:boolean;revision?:number;changes?:{revision:number;kind:string;status:string;source_message_id:number;change:{objective:string;summary?:string};created_at:string}[];directions?:{id:string;task_id:string;revision:number;status:string;error:string|null}[];outcomes_version?:number;outcomes?:WorkOutcome[];progress?:{fulfilled:number;required:number};current_task:Task|null; executions:Task[]; outputs:Artifact[]; updated_at:string};
export type InputRequest = {id:string;task_id:string;work_item_id:string|null;prompt:string;answer_type:'text'|'choice'|'confirmation';choices:string[];question_hash:string;status:string;expires_at:string;answer?:string|null};
export type Approval = {id: string; task_id: string; kind: string; payload: Record<string, unknown>; payload_hash: string; status: string; expires_at: string};
export type Artifact = {id: string; task_id: string; name: string; content_type: string; size: number; bot_id: BotId};
export type Attachment = {id: string; name: string; content_type: string; size: number; sha256: string};
export type Message = {id: number; task_id: string; source_id:string; work_item_id?:string|null; message_intent?:string; author: string; role: string; content: string; created_at: string; edited_at?: string; attachment_ids: string[]; mentions: BotId[]; reply_to_message_id: number | null; reply_count: number; reactions: {emoji:string; author:string}[]};
export type Provider = {id:string; name:string; base_url:string; models:string[]; default_model:string; revision:number; key_configured?:boolean;auth_kind?:'api_key'|'chatgpt_oauth'|'codex_oauth';transport?:string;connection_status?:string;account_label?:string;account_plan?:string;plan_enabled?:boolean;verified_models?:string[];discovered_models?:string[]};
export type Workspace = {timezone:string; default_provider_id:string | null; permitted_tools:string[]; max_bots:number; max_active:number; active_budget_seconds:number; decision_provider_id:string|null; decision_model:string; decision_mode:string; decision_actions:boolean;external_action_mode?:'requested'|'review'};
export type Grant = {id:string; manager_id:BotId; targets:BotId[]; operations:string[]; kind:string; expires_at:string; revoked_at:string|null; max_created_bots:number; created_bots:number; max_memory_mb:number; max_cpus:number};
export type Proposal = {id:string; task_id:string; approval_id:string; bot_id:BotId; action:string; status:string; payload:Record<string,unknown>};
export type Operation = {id:string; bot_id:BotId; action:string; status:string; error:string|null};
export type Control = {id:string; task_id:string; input:string; status:string};
export type Settings = {workspace:Workspace; providers:Provider[];model_broker_online?:boolean};
export type State = {input_requests?:InputRequest[];work_items:WorkItem[]; bots: Bot[]; retired_bots: Bot[]; conversations: Conversation[]; tasks: Task[]; approvals: Approval[]; artifacts: Artifact[]; grants:Grant[]; proposals:Proposal[]; operations:Operation[]; controls:Control[]; workspace:Workspace; provider_configured: boolean; protocol_fixture: boolean; event_cursor: number};
export type Chat = Conversation & {messages: Message[]; artifacts:Artifact[]; attachments:Attachment[]; has_earlier: boolean;thread_root?:Message|null};
export type Event = {id: number; kind: string; task_id: string | null; conversation_id: string | null; bot_id: BotId | null; data: Record<string, unknown>;created_at:string};

// A refused request keeps the server's status and, where it gives one, a code naming what it needs.
export type ApiError = Error & {status?: number; code?: string; warnings?: string[]};

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {credentials: 'same-origin', ...init, headers: {'Content-Type': 'application/json', ...init.headers}});
  if (!response.ok) {
    if(response.status===401) window.dispatchEvent(new Event('hermbot-session-expired'));
    const body = await response.json().catch(() => ({}));
    const failure: ApiError = new Error(typeof body.detail === 'string' ? body.detail : body.detail?.message ? `${body.detail.message} ${body.detail.recovery||''}`.trim() : `Request failed (${response.status})`);
    failure.status = response.status;
    if (typeof body.detail?.code === 'string') failure.code = body.detail.code;
    if (Array.isArray(body.detail?.warnings)) failure.warnings = body.detail.warnings.filter((warning:unknown):warning is string=>typeof warning==='string');
    throw failure;
  }
  return response.json();
}

export const settled = (task: Task) => ['completed', 'failed', 'cancelled', 'interrupted', 'uncertain'].includes(task.status);
export const formatFileSize = (size:number) => size<1024?`${size} B`:`${(size/1024).toFixed(1)} KB`;
export const statusLabel = (status: string) => ({paused_budget:'Budget reached',pausing_budget:'Stopping at budget limit',planning:'Planning',working:'Working',needs_answer:'Needs answer',waiting_input:'Needs answer',needs_approval:'Needs approval',needs_attention:'Needs attention',pending:'Pending',fulfilled:'Ready',queued: 'Queued', blocked: 'Waiting for dependencies', running: 'Working', waiting_children: 'Waiting for teammates', waiting_results: 'Waiting for required results',submitted:'Submitted for review',accepted:'Accepted', waiting_approval: 'Needs your review', waiting_computer: 'Waiting for computer handback', completed: 'Completed', failed: 'Failed', cancelling: 'Stopping', cancelled: 'Stopped', uncertain: 'Needs reconciliation', interrupted: 'Interrupted', scheduled: 'Scheduled'}[status] || status);
export function taskStatus(task:Task) {
  if(task.status==='queued'&&task.message_intent==='auto'&&task.orchestration_version===2)return task.intake_status==='failed'?'Could not interpret message':'Understanding your message';
  if(task.status==='waiting_approval'){
    if(task.connector_state==='uncertain')return 'Check application outcome';
    if(task.connector_state==='queued')return 'Waiting for application';
    if(task.connector_state==='dispatching')return 'Applying approved change';
  }
  return statusLabel(task.status);
}
export function executionSummary(tasks:Task[]) {
  const pending=tasks.filter(task=>!settled(task)||task.status==='uncertain');
  const priority:Record<string,number>={uncertain:0,cancelling:1,pausing_budget:1,running:2,waiting_approval:3,waiting_computer:3,waiting_input:3,waiting_children:4,waiting_results:4,blocked:5,paused_budget:6,queued:7,scheduled:8};
  const task=pending.reduce<Task|undefined>((selected,candidate)=>!selected||(priority[candidate.status]??9)<(priority[selected.status]??9)?candidate:selected,undefined);
  const queued=pending.filter(task=>task.status==='queued').length;
  return {task,queued,executing:!!task&&['running','cancelling','pausing_budget'].includes(task.status),
    label:task?`${taskStatus(task)}${queued&&task.status!=='queued'?` · ${queued} queued`:''}`:''};
}
export function requestKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

const mutationKeys = new Map<string,string>();
export async function mutate<T>(path:string,method:string,body:unknown):Promise<T> {
  const fingerprint=method+path+JSON.stringify(body);
  if(!mutationKeys.has(fingerprint)) mutationKeys.set(fingerprint,requestKey());
  const result=await api<T>(path,{method,headers:{'Idempotency-Key':mutationKeys.get(fingerprint)!},body:JSON.stringify(body)});
  mutationKeys.delete(fingerprint);
  return result;
}
export function clearMutationKeys() {mutationKeys.clear();}
