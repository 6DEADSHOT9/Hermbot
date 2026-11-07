from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

BotId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")]
ManagementOperation = Literal["delegate", "inspect", "steer", "cancel", "reassign", "create", "configure", "start", "stop"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskRequest(StrictModel):
    bot_id: BotId
    conversation_id: UUID
    prompt: str = Field(min_length=1, max_length=50_000)
    managed_bot_ids: list[BotId] | None = Field(default=None, max_length=9)
    management_operations: list[ManagementOperation] | None = Field(default=None, max_length=9)
    grant_id: UUID | None = None
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=10)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=10)
    reply_to_message_id: int | None = Field(default=None, ge=1)
    skill_refs: list[str] = Field(default_factory=list, max_length=10)
    mention_bot_ids: list[BotId] = Field(default_factory=list, max_length=6)
    deadline: datetime | None = None


class GroupRequest(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    bot_ids: list[BotId] = Field(min_length=2, max_length=6)


class MessageRequest(TaskRequest):
    intent: Literal['auto', 'discussion', 'assignment', 'update', 'status'] = 'auto'
    work_item_id: UUID | None = None


class RouteMessageArgs(StrictModel):
    intent: Literal['discussion', 'assignment', 'update', 'status', 'answer']
    work_item_id: UUID | None = None
    title: str = Field(default='', max_length=120)
    input_request_id: UUID | None = None
    answer: str | None = Field(default=None, min_length=1, max_length=5000)


class WorkMessageRequest(StrictModel):
    task_id: UUID
    intent: Literal['discussion', 'assignment', 'update', 'status']
    work_item_id: UUID | None = None
    title: str = Field(default='', max_length=120)


class SteerRequest(StrictModel):
    prompt: str = Field(min_length=1, max_length=10_000)


class DecisionRequest(StrictModel):
    decision: Literal["once", "deny", "cancel"]
    expected_payload_hash: str = Field(min_length=64, max_length=64)


class ReviewEditRequest(StrictModel):
    expected_payload_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    summary: str = Field(min_length=1, max_length=2000)
    payload: dict


class RoutineRequest(TaskRequest):
    scheduled_at: datetime


class ToolRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=200)
    receipt_id: str = Field(min_length=1, max_length=300)
    operation: Literal["teammates", "delegate", "message", "wait", "status", "work_status", "review", "ask", "publish", "publish_file", "read_artifact", "reassign", "steer", "cancel", "finish", "propose", "grants", "proposals", "connectors", "connector", "route_message", "prepare"]
    arguments: dict
    expected_work_revision: int | None = Field(default=None, ge=1)


class EmptyArgs(StrictModel):
    pass


class ConnectorArgs(StrictModel):
    connection_id: UUID | None = None
    connection: str = Field(default='', max_length=200)
    action: str = Field(min_length=1, max_length=120)
    arguments: dict = Field(default_factory=dict)
    summary: str = Field(default='', max_length=2000)
    outcome_id: UUID | None = None
    outcome_key: str = Field(default='', max_length=80)


class OutcomeTarget(StrictModel):
    connection_id: UUID | None = None
    connection_name: str = Field(default='', max_length=200)
    action: str = Field(default='', max_length=120)
    resource: str = Field(default='', max_length=4000)
    display_name: str = Field(default='', max_length=300)


class OutcomeSpec(StrictModel):
    key: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,79}$')
    title: str = Field(min_length=1, max_length=300)
    kind: Literal['report', 'artifact', 'connector_effect', 'execution_check', 'native_effect']
    required: bool = True
    target: OutcomeTarget = Field(default_factory=OutcomeTarget)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    expected_revision: int | None = Field(default=None, ge=1)


class OutcomeClaim(StrictModel):
    outcome_id: UUID
    expected_revision: int = Field(ge=1)
    status: Literal['fulfilled', 'blocked', 'failed', 'uncertain']
    reason: str = Field(default='', max_length=2000)
    artifact_id: UUID | None = None
    operation_id: UUID | None = None
    review: str = Field(default='', max_length=5000)
    reviewed_task_ids: list[UUID] = Field(default_factory=list, max_length=30)


class AskArgs(StrictModel):
    prompt: str = Field(min_length=1, max_length=2000)
    answer_type: Literal['text', 'choice', 'confirmation'] = 'text'
    choices: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(default_factory=list, max_length=20)
    outcome_id: UUID | None = None


class AnswerRequest(StrictModel):
    expected_question_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    answer: str = Field(min_length=1, max_length=5000)


class OutcomeRevisionRequest(StrictModel):
    expected_revision: int = Field(ge=1)


class OutcomeReconcileRequest(OutcomeRevisionRequest):
    result: Literal['fulfilled', 'failed', 'uncertain']
    evidence: str = Field(min_length=10, max_length=2000)


class ConnectionRequirement(StrictModel):
    connection_id: UUID
    action: str = Field(min_length=1, max_length=120)
    resource: str | None = Field(default=None, max_length=500)


class PrepareArgs(StrictModel):
    skills: list[Annotated[str, Field(pattern=r'^[a-z][a-z0-9-]{0,79}$')]] = Field(default_factory=list, max_length=3)
    tools: list[Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,119}$')]] = Field(default_factory=list, max_length=30)
    connectors: list[ConnectionRequirement] = Field(default_factory=list, max_length=10)
    outcomes: list[OutcomeSpec] = Field(default_factory=list, max_length=30)


class ToolVisibilityRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=200)
    tools: list[Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,119}$')]] = Field(max_length=150)


class DelegateArgs(StrictModel):
    bot_id: BotId
    prompt: str = Field(min_length=1, max_length=30_000)
    depends_on: list[UUID] = Field(default_factory=list, max_length=10)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=10)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=10)
    skill_refs: list[str] = Field(default_factory=list, max_length=3)
    expected_output: str = Field(default='', max_length=2000)
    outcome_ids: list[UUID] = Field(default_factory=list, max_length=30)
    outcome_keys: list[str] = Field(default_factory=list, max_length=30)
    responsibility: Literal['contribution', 'deliverable'] = 'contribution'
    required: bool = True
    replaces_task_id: UUID | None = None


class PeerMessageArgs(StrictModel):
    bot_id: BotId
    prompt: str = Field(min_length=1, max_length=10_000)
    reply_to: UUID | None = None


class WaitArgs(StrictModel):
    task_ids: list[UUID] = Field(default_factory=list, max_length=30)
    message_ids: list[UUID] = Field(default_factory=list, max_length=30)


class InboxDeliveryRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=200)
    message_ids: list[UUID] = Field(max_length=30)


class TaskArgs(StrictModel):
    task_id: UUID


class WorkStatusArgs(StrictModel):
    work_item_id: UUID | None = None


class ReviewArgs(StrictModel):
    summary: str = Field(min_length=1, max_length=2000)
    payload: dict = Field(default_factory=dict)
    kind: Literal["review", "input", "skill", "memory", "connector"] = "review"


class PublishArgs(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    content: str = Field(max_length=1_000_000)
    content_type: Literal["text/plain", "text/markdown", "text/csv", "application/json", "text/x-python"] = "text/plain"
    previous_id: UUID | None = None
    outcome_id: UUID | None = None
    outcome_key: str = Field(default='', max_length=80)


class PublishFileArgs(StrictModel):
    path: str = Field(min_length=1, max_length=2000)
    name: str = Field(default='', max_length=160)
    content_type: str = Field(min_length=1, max_length=200)
    previous_id: UUID | None = None
    outcome_key: str = Field(default='', max_length=80)
    outcome_id: UUID | None = None


class ReadArgs(StrictModel):
    artifact_id: UUID


class ReassignArgs(TaskArgs):
    bot_id: BotId


class ToolSteerArgs(TaskArgs):
    prompt: str = Field(min_length=1, max_length=10_000)


class FinishArgs(StrictModel):
    result: str = Field(min_length=1, max_length=50_000)
    outcomes: list[OutcomeClaim] = Field(default_factory=list, max_length=30)
    results: list['WorkResult'] = Field(default_factory=list, max_length=30)


class WorkResult(StrictModel):
    key: str = Field(min_length=1, max_length=80)
    status: Literal['fulfilled', 'blocked', 'failed', 'uncertain']
    reason: str = Field(default='', max_length=2000)
    artifact_id: UUID | None = None
    operation_id: UUID | None = None
    review: str = Field(default='', max_length=5000)
    reviewed_task_ids: list[UUID] = Field(default_factory=list, max_length=30)


class BotConfiguration(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=8000)
    color: str = Field(default="#829c77", pattern=r"^#[0-9a-fA-F]{6}$")
    avatar: str = Field(default="", max_length=8)
    provider_id: UUID | None = None
    model_name: str = Field(default="", max_length=200)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    enabled_tools: list[Literal["file", "terminal", "skills", "memory", "browser", "computer_use"]] = Field(default_factory=lambda: ["file", "terminal"])
    enabled_skills: list[str] = Field(default_factory=list, max_length=20)
    memory_mb: int = Field(default=2048, ge=512, le=8192)
    cpus: float = Field(default=1.5, ge=0.25, le=4)


class BotUpdate(BotConfiguration):
    expected_revision: int = Field(ge=1)
    interrupt: bool = False


class CloneRequest(StrictModel):
    configuration: BotConfiguration
    copy_memory: bool = False
    copy_skills: bool = False
    copy_workspace: bool = False
    copy_history: bool = False
    expected_revision: int = Field(ge=1)


class LifecycleRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    interrupt: bool = False
    confirmation: str | None = None


class ProviderRequest(StrictModel):
    auth_kind: Literal['api_key', 'chatgpt_oauth', 'codex_oauth'] = 'api_key'
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=1, max_length=2000)
    models: list[str] = Field(min_length=1, max_length=100)
    default_model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4000)
    expected_revision: int | None = Field(default=None, ge=1)


class WorkspaceRequest(StrictModel):
    timezone: str = Field(max_length=100)
    default_provider_id: UUID | None = None
    permitted_tools: list[Literal["file", "terminal", "skills", "memory", "browser", "computer_use"]]
    max_bots: int = Field(default=10, ge=3, le=30)
    max_active: int = Field(default=3, ge=1, le=10)
    active_budget_seconds: int = Field(default=3600, ge=60, le=86400)
    decision_provider_id: UUID | None = None
    decision_model: str = Field(default='', max_length=200)
    decision_mode: Literal['disabled', 'decisions', 'chat', 'responses'] = 'disabled'
    decision_actions: bool = False
    external_action_mode: Literal['requested', 'review'] | None = None


class GrantRequest(StrictModel):
    manager_id: BotId
    targets: list[BotId] = Field(max_length=20)
    operations: list[ManagementOperation] = Field(min_length=1, max_length=9)
    duration_hours: int = Field(default=24, ge=1, le=720)
    max_created_bots: int = Field(default=1, ge=0, le=5)
    max_memory_mb: int = Field(default=2048, ge=512, le=8192)
    max_cpus: float = Field(default=1.5, ge=0.25, le=4)


class ProposalArgs(StrictModel):
    action: Literal["create", "configure", "start", "stop"]
    bot_id: BotId | None = None
    configuration: BotConfiguration | None = None
    summary: str = Field(min_length=1, max_length=2000)
    interrupt: bool = False


class ReactionRequest(StrictModel):
    emoji: Literal["👍", "❤️", "✅", "👀", "🎉", "❓"]


class EditRequest(StrictModel):
    prompt: str = Field(min_length=1, max_length=50_000)
    expected_input_hash: str = Field(min_length=64, max_length=64)


class RetryRequest(StrictModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=50_000)


class AttachmentRequest(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    content_type: str = Field(max_length=150)
    content_base64: str = Field(max_length=14_000_000)


ARGUMENT_MODELS = {
    "teammates": EmptyArgs, "delegate": DelegateArgs, "message": PeerMessageArgs, "wait": WaitArgs, "status": TaskArgs, "work_status": WorkStatusArgs,
    "review": ReviewArgs, "ask": AskArgs, "publish": PublishArgs, "publish_file": PublishFileArgs, "read_artifact": ReadArgs, "reassign": ReassignArgs,
    "steer": ToolSteerArgs, "cancel": TaskArgs, "finish": FinishArgs,
    "propose": ProposalArgs, "grants": EmptyArgs, "proposals": EmptyArgs,
    "connectors": EmptyArgs, "connector": ConnectorArgs,
    "route_message": RouteMessageArgs, "prepare": PrepareArgs,
}


class ContextRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=200)
    routine_id: UUID | None = None
    routine_ticket: str | None = Field(default=None, max_length=100)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=200)
    tool_name: str | None = Field(default=None, min_length=1, max_length=100)


class EndRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=200)
    completed: bool
    interrupted: bool = False
    failed: bool = False
