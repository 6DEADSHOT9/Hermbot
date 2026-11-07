# Phase 5.3 — Recurring orchestration: researched implementation plan

Recorded October 26, 2026. Implementation is deployed locally. **Milestone acceptance and fixture cleanup are complete.** Two native scheduled occurrences, one signed-local-event manager occurrence and one actual owner-answer/restart continuation passed. All 581 backend tests pass; [review and corrections](PHASE5-ORCHESTRATION-REVIEW.md) and the evidence below record the exact coverage. Sections 1–16 retain the researched design; section 17 records delivered behavior and remaining acceptance.

## 1. Outcome, scope and sequencing

An owner can save a scheduled responsibility for a manager and specified teammates. Each occurrence creates ordinary durable work, coordinates permitted contributions, waits for real dependencies or human decisions, resumes with current authority, and delivers verified results to its configured conversation without an open browser.

Finish this milestone, Phase 5.4 and the Phase 5 exit before the separately deferred [connected-service coordination work](CONNECTED-WORK-COORDINATION.md). Phase 5.3 does not introduce shared admission across overlapping independent triggers, semantic cross-service deduplication, a new provider platform, mobile use, VM isolation or public hosting. Existing phase numbering and evaluation-before-issue-backlog-fixes ordering remain unchanged. Focused milestone acceptance is not the deferred broad evaluation suite.

Keep Hermes source unmodified and owner-enabled native tools available. Reuse its existing supported no-agent cron wake-up, ordinary authenticated runtime execution and Hermbot's coordination plugin. Do not introduce periodic model checkpoints, forced time-sliced yielding or another agent execution protocol. Durable application state records real waits and completed effects.

The primary workflow is scheduled management. Also carry the same optional management configuration through Phase 5.2's existing saved-work/event admission path and demonstrate one event-triggered orchestration. This reuses existing sources and does not implement the postponed coordination of independent triggers.

Phase 5.4 retains the new attention inbox, browser push, quiet hours and notification preferences. Phase 5.3 supplies accurate run status and existing conversation/review links so its own behavior is usable and verifiable.

## 2. Existing implementation and gaps

Read-only source inspection on October 26 identified these reusable foundations. Reinspect them when implementation starts; this is not a claim of freshly run tests.

| Existing code | Reuse | Gap or integration requirement |
|---|---|---|
| `backend/routine_models.py`, `routines.py`, migration 014 | Immutable saved revisions, unique occurrences, native wake binding, start window, skip/latest missed-run behavior, daily cap, overlap checks and history | Saved definitions have no management binding; `create_saved_task` does not pass a grant into `service.create_task`. |
| `backend/registry.py:create_grant`, `models.py:GrantRequest`, `app.py:revoke_grant` | Standing grants, targets/operations, expiry, revocation and creation/resource limits | Bind a routine to a real owner-issued grant, restrict it to selected collaborators/operations and expose expiry. Revoking a shared grant currently stops its owned roots. |
| `backend/service.py` | Task identity, delegation, grant checks, handoff/depth bounds, explicit artifacts and execution recovery | Effective saved-run scope must reach every caller, including descendant and replacement assignments. Avoid an ungranted fallback. |
| `backend/coordination.py` | Informational inbox, explicit waits, dependency readiness and result-driven continuation | `schedule_inboxes` creates separate peer-response roots without the source grant; associate automated replies with their originating run and enforce current authority without turning messages into assignments. |
| `backend/execution_policy.py` | Active-time budget aggregated for assignment/update attempts in a work item, budget pause and owner continuation | Automatically spawned discussion/peer-response attempts currently have separate budget treatment. Charge their execution to the run without charging unrelated owner status/discussion turns. |
| `backend/work_intake.py`, `work_items.py`, `work_controller.py` | Automatic message association, owner corrections, work revisions, durable controls | Corrections/retries can create another root for the same work. Preserve occurrence, scope and budget rather than looking only at the original root. |
| `backend/result_acceptance.py`, `outcomes.py` | Accountable deliverable owner, required/optional contributions, review and receipt evidence | Use these acceptance rules for manager completion and incremental-window advancement. |
| `backend/worker.py`, `execution_monitor.py` | Slot admission, native execution identity, event recovery, genuine waits, bounded safe recovery | Recheck effective authority and budget at claim/resume. An ending turn, heartbeat or accepted dispatch is not completed work. |
| `backend/connections.py`, `inputs.py`, lifecycle/review handlers | Exact payload reviews, input hashes, connection permission, dispatch receipts and uncertain outcomes | Validate saved-run scope and current authority on decisions and dispatch. Preserve existing independently authenticated native-tool coverage limits. |
| `backend/triggers.py`, `trigger_models.py`, `trigger_api.py` | Saved-work admission, event history, source authentication and provider cursors | Optional management uses the same contract; existing non-manager triggers retain behavior. |
| `web/src/Routines.tsx`, `EventTriggers.tsx`, `Management.tsx`, `api.ts` | Routine editor/history, event configuration, standing-grant controls, normal work/review navigation | Add collaborator/permission configuration and precise run status without a separate workflow designer. |

Specific correctness cases require inspection: a routine edit changes its revision-based occurrence key; first-claim validation returns early after any attempt starts; revocation/dispatch must agree on a locking boundary; work revisions may acquire a different root; informational responses can outlive the source assignment. Include deterministic regressions instead of assuming existing single-bot acceptance covers these combinations.

## 3. Lessons from comparable implementations

Sources are primary documentation or original project reports, reviewed October 26, 2026. Recommendations below are our application of their patterns. Framework internals and release-specific behavior must be rechecked before adoption; no new dependency is selected.

| Reference | How the comparable system works | Lesson for 5.3 |
|---|---|---|
| [Hermes cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron) | Supports scheduled scripts without an agent, fresh agent runs, skills and delivery controls. | Retain the proven no-agent wake job. Hermbot admits the manager with recorded authority; native cron does not become a second application scheduler. |
| [Hermes delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation) | Native subagents have context/tool/lifetime rules tied to their parent session; the docs distinguish longer-lived work. | Existing persistent Hermbot teammates remain application-managed assignments. Do not substitute native ephemeral children for durable teammate ownership or claim documentation proves deployed-version behavior. |
| [Temporal Schedules](https://docs.temporal.io/schedule) | Separates a schedule from executions; defines overlap, catch-up and pause behavior. Schedule pause stops future actions, not started workflows. | Keep saved definition, occurrence and execution separate. Preserve one unfinished occurrence and bounded missed-run handling; distinguish pause from stop. Workflow Pause is marked pre-release in the reviewed docs and is not an implementation dependency. |
| [Kubernetes CronJob](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/) | Controllers create jobs from desired schedules; duplicate/missing creation is possible. Concurrency policies apply within one CronJob. | A native tick is a wake signal, not exactly-once authority. Durable occurrence admission and idempotent effects remain necessary; per-routine overlap does not solve overlap across independent routines. |
| [Argo CronWorkflows](https://argo-workflows.readthedocs.io/en/latest/cron-workflows/) | Uses explicit concurrency policy, missed-start grace and failure-based scheduling controls. Already-started work can exceed a scheduling stop threshold. | Keep current skip/latest and failure-pause semantics, account for whole-run completion and avoid an unbounded backlog after downtime. |
| [Airflow Dag Runs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dag-run.html) | Gives each run a logical identity and data interval. Catch-up starts missing intervals when enabled; permissive leaf rules can conceal earlier failures. | Freeze data windows independently of actual start time. Manager prose or a successful final summarizer must not hide missing required outcomes. |
| [Airflow deferrable operators](https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/deferring.html) | Idle work can release a worker slot and resume from an external trigger. | Real contribution/approval waits should not occupy execution capacity or repeatedly invoke the model. Maintain bot exclusivity until the native run actually settles. |
| [Prefect interactive workflows](https://docs.prefect.io/v3/advanced/interactive) | Supports typed input and correlated messages to waiting runs; custom validation still needs application handling. | Reuse typed owner questions and exact run/question references. An answer is data for existing work, not authority for an unrelated action. |
| [Step Functions callback integration](https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html) | A durable workflow waits for a correlated task-token result; timed-out callback attempts get a different token. | Bind replies/reviews to the current request and revision. Reject stale decisions and coalesce duplicate decisions into one continuation. |
| [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | Resumption may reexecute node code; its docs warn that prior side effects must be idempotent. | Reuse existing results and operation receipts on continuation. Do not restart a manager prompt from scratch or import a checkpoint system into Hermes. |
| [AWS IAM PassRole](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_passrole.html) | Separates ability to configure execution from permission to attach a particular execution role. | Saving a schedule cannot mint management authority. Binding it to a grant is an authenticated owner action with an explicit restricted scope. |
| [Scheduler Agent Supervisor](https://learn.microsoft.com/en-us/azure/architecture/patterns/scheduler-agent-supervisor) | Persists step state and coordinates workers and recovery through asynchronous messages and supervision. | The backend supervises durable progress; the manager reasons about the requested work. Logical supervisor roles need not be new processes or extra LLM agents. |
| [Temporal cancellation](https://docs.temporal.io/encyclopedia/workflow/cancellation-and-termination) and [child workflows](https://docs.temporal.io/child-workflows) | Cancellation is cooperative; parent closure needs an explicit child policy. Termination cannot run cleanup. | Stop the owned tree and wait for settlement; do not treat a stop request as a receipt or leave children orphaned. |
| [Compensating transactions](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction) | Undo is business-specific and can conflict with later changes; an alternative recovery may be preferable. | Retain partial successes. Do not automatically delete files, retract messages or undo successful writes because a later branch fails. Any compensation needs its own authority. |
| [Cloud Scheduler](https://docs.cloud.google.com/scheduler/docs/overview) | Documents at-least-once delivery and an original scheduled-time header stable across retries. | Separate occurrence identity from callback/attempt identity. This page was updated October 22, 2026 when reviewed. |

### Real incidents and proposed fixes worth testing against

- [Prefect #18894](https://github.com/PrefectHQ/prefect/issues/18894), opened September 2025 with discussion continuing in 2026: users report duplicate runs at one expected time around schedule changes. A [September 22, 2026 proposed fix, #23175](https://github.com/PrefectHQ/prefect/pull/23175), preserves schedule IDs during upsert so retry keys remain stable. It was **open** when reviewed; do not describe it as a shipped fix. Lesson: changing configuration identity must not accidentally mean a new occurrence. Serialize edits/admission and exercise the interleaving explicitly.
- [Prefect #19403](https://github.com/PrefectHQ/prefect/issues/19403): an original report describes a bounded candidate batch filled with concurrency-blocked work, starving otherwise runnable deployments. Lesson: a waiting manager or blocked branch must not occupy every eligible queue candidate. Test that independent work can run.
- [Prefect #22386](https://github.com/PrefectHQ/prefect/issues/22386), reporting a May 2026 build: queued runs reportedly wait many minutes after capacity becomes free. This is a report, not a verified cause or general product property. Lesson: measure time between dependency/capacity release and dispatch; an honest queued state needs a reason and timely reevaluation.

## 4. Owner workflow and defaults

Use the existing Routines editor, not a separate automation product:

1. Choose a results conversation and its manager teammate.
2. Enable **Coordinate teammates**, select explicitly permitted teammates and an existing active standing grant for that manager. If none fits, use the existing owner grant-creation flow and return to the draft. Creating a grant does not activate the routine.
3. Enter instructions, expected results, selected input versions, schedule/timezone and current execution settings. The manager decides the task breakdown during each run.
4. Inspect a compact summary: manager, collaborators, management operations, grant expiry, results destination, next starts, active-work budget and external-action review behavior.
5. Save to activate, or retain a draft/paused definition. A natural-language request prepares an inactive draft; it never grants management power or activates recurring work by itself.
6. Inspect each run through existing work, contribution, artifact and review surfaces. Answering a run's question or review resumes that occurrence; it does not create another scheduled run.

Defaults preserve 5.1: one unfinished occurrence, skip overlap, skip missed work unless the owner selected latest-within-window, daily admission cap, existing active-work budget, existing maximum active executions, no automatic grant renewal and no automatic increase in budget.

The initial recurring-management operations are the existing `delegate`, `inspect`, `steer`, `cancel` and `reassign` permissions, constrained by the selected grant. `delegate` is required. Do not grant a permission that the standing grant lacks; allow a useful subset instead of requiring all operations. Bot creation/configuration/runtime start/stop remain existing separately reviewed manual-management capabilities and are not newly automated by 5.3. A broad standing grant must not make those operations available inside a restricted recurring run.

The owner can select available registered collaborators without adding them to a private conversation. The manager must be a participant in the destination; contributor access uses the existing explicit handoff. Do not expose private transcripts, browser sessions, memories or credentials merely because a collaborator is selected.

## 5. Saved configuration and durable run binding

Extend `SavedWorkDefinition` with one optional typed management object, inherited by both routine and trigger definitions. Proposed shape:

```json
{
  "management": {
    "grant_id": "standing-grant-uuid",
    "targets": ["existing-teammate-id", "another-teammate-id"],
    "operations": ["delegate", "inspect", "steer", "cancel", "reassign"]
  }
}
```

The existing `bot_id` is the manager. Omission/null retains existing single-teammate behavior. Normalize duplicate targets/operations, require nonempty targets, reject self/unknown targets and unknown operations, and enforce current field/count bounds. Reject a supplied invalid binding rather than silently stripping it and executing ungranted.

Do not accept actor identity, effective scope, grant expiry or run snapshot from the model or browser. On authenticated save/activation, validate the selected grant is standing, active, belongs to the manager and covers the explicit targets and operations. Preserve immutable revision/source-message provenance and current owner identity. The application remains single-owner; do not build Phase 8 membership to record that identity honestly.

At admission, pass the grant into ordinary task creation and persist the server-derived run binding with the existing routine/trigger context. Record saved revision, owner/source reference, selected scope, grant reference and observed expiry, destination, frozen inputs/results/window, budget and admitted occurrence. A copied expiry is explanatory evidence; live expiry/revocation still wins.

Prefer existing run/context records and task run-ID columns. Add schema only where durable association cannot otherwise be enforced; no second workflow or approval database. Derive effective scope from immutable admitted data, never mutable free-form `handoff_context` alone.

Every automated assignment, replacement root, owner-directed revision of that occurrence, continuation and peer-response execution must remain associated with the originating run. Reuse existing `routine_run_id`/`trigger_run_id` and work identity where possible, with validated linkage. Work association is not authorization. Do not attach unrelated new owner work to a historical run simply because it is in the same thread.

## 6. Effective authority and expiry

For each application-managed action, effective management authority is the intersection of the current owner-issued grant, the saved run's selected scope, current workspace policy, task/work state and the caller's existing manager/contributor role. Connector actions independently require the actual acting teammate's connection, resource and owner-action permissions.

Apply the same effective view to:

- Admission, first dispatch, safe recovery, continuations and budget continuation.
- Teammate discovery/capability projection, informational messaging, delegation, nested delegation, inspection, steering, cancellation and reassignment.
- Generated peer replies and other automated executions outside the original root.
- Review/answer decisions, connector dispatch and any path that can adopt a grant during work revision.

Do not pick another valid standing grant automatically when the selected one expires. Do not widen targets after a shared grant changes or a new bot is created elsewhere. A grant that was valid at save time is not necessarily valid at the next occurrence or when a user answers tomorrow.

Keep existing finite grant durations (currently 1–720 hours). Show the authorization end date and whether the next scheduled occurrence is beyond it. On expiry/revocation, prevent new starts, pause affected definitions with an actionable reason and settle/cancel their owned pending execution according to the existing revocation contract. A paused definition remains paused after replacement authorization until the owner explicitly saves/resumes it.

Renewal uses an explicit valid grant selection and saved revision; it authorizes future occurrences. It must not resurrect cancelled old tasks or stale approvals. Resume an unfinished old occurrence only through a separately supported owner action with reconciled outcomes and fresh authority; do not quietly rebind it during routine editing.

For concurrency between revocation and dispatch, define and enforce a consistent database lock/admission order with existing routine, work/root, grant, bot and connector locks. Revocation and dispatch must share the authority decision boundary, including a durable operation marker. An external request already admitted may finish after revocation; retain its receipt or uncertainty. Do not promise remote rollback or instantaneous interruption of native tools.

## 7. Admission, identity and scheduling behavior

Reuse the one per-bot native no-agent wake job. It carries a scoped generation/token, not instructions or authority. The backend uses database time and the saved definition to decide what is due; native cron does not execute the manager prompt directly.

Within a short transaction, lock the definition, resolve the occurrence, validate current scope/destination/inputs/runtime, check unfinished work and limits, then create the run, thread, work item and root assignment together. A definite admission rejection leaves an inspectable skipped/rejected reason rather than a partially admitted manager. Preserve idempotent owner mutation receipts and bounded native wake retries.

Required identity rules:

- Duplicate wake-ups and concurrent Run now submissions with one idempotency key return one admitted result.
- A schedule tick uses the intended UTC occurrence, not current callback time or a fresh random execution identity.
- Definition revisions are execution snapshots, not permission to replay an already admitted scheduled instant. Serialize an edit/tick race and check the same routine/time across revisions where necessary, retaining historical keys and records.
- A genuinely requested manual run has its own owner idempotency key and preserves the next automatic occurrence. Run now respects current authority, overlap and daily limits even while a routine is paused.
- Manager/target/instruction/schedule edits affect future and unstarted work. Started work retains its admitted revision, except explicit current-work directions and current permission reductions.
- Start-window expiry applies before the occurrence's first execution. A valid started run waiting for a human may resume later if its authority, review and optional deadline remain valid; do not reuse the original start window as an execution timeout.

Keep minute resolution, IANA timezone calculation, existing DST/month-end semantics and skip/latest missed-run policy. Do not introduce backfill-all, replacement-on-overlap or native and backend double catch-up. Long downtime records bounded skip history, not millions of jobs. No catch-up is authorized merely by replacing a grant or restoring a conversation.

One unfinished run includes live descendants, required contributions, budget pauses, questions/reviews, pending/uncertain connector effects and linked automated continuations. A terminal manager row cannot release overlap while relevant work is still active or uncertain.

## 8. Execution and contribution contract

The manager receives admitted instructions, expected results, data window, selected input references, current permissible collaborators and limits through existing authenticated execution context. Provider payloads, tool output and teammate replies remain untrusted data. Do not copy full private histories or secrets into a shared run prompt.

Reuse asynchronous delegation, dependencies, explicit required/optional contributions, file grants and accountable deliverable ownership. Managers may create independent branches and work while they run. They wait only when named results or an actual owner decision are necessary. Existing cycle, ancestor, depth and handoff limits prevent circular delegation and uncontrolled fan-out.

Nested assignment remains within the original selected scope. A contributor cannot acquire manager-only operations, expand target scope or turn a peer message into owner work. Informational messages create no contribution requirement; automated replies remain bounded and scoped. Do not send messages to additional teammates simply to keep the manager active.

Unavailable selected teammates are visible as exact blockers. The manager may use another selected capable teammate or perform permitted work itself if it still meets the request. It may not silently replace an explicitly required specialist, add an unselected bot, start/configure a runtime or bypass required results. If no allowed path exists, explain what is missing and retain the failed/blocked branch for inspection.

Required contribution failure wakes the manager with its evidence, even while another branch waits. Optional failure does not falsely fail a deliverable but must be disclosed if it affects the requested result. Reassignment uses current authority and the existing settled-attempt/uncertain-effect checks; no simultaneous replacement of a potentially running old assignment.

Completion requires fulfilled current required outcomes, accepted required contributions and verified external receipts as applicable. A child's summary, an artifact's existence, a completed native turn or all jobs being terminal is insufficient. A final report should identify the result, relevant artifacts/receipts and any limitations.

Do not leave optional children mutating after a run is reported settled. Either await them or explicitly cancel and settle them, preserving any effects already admitted. Optional work still running or uncertain remains visible and blocks a misleading fully settled status.

## 9. Budgets, capacity and elapsed time

Keep one aggregate active-work allowance per occurrence/work item, shared by the manager, descendants, retries and automatically generated peer replies. Two agents working for one minute consume roughly two agent-minutes. Awaiting contributions, owner input or approval does not consume active model-execution time once the run has actually stopped executing.

Audit every claim/continuation and automated root creation. Reconcile active accounting before checking remaining budget and include concurrently running attempts; do not let each child assume it has the whole remaining allowance. Resource accounting has a polling/in-flight margin and is not a guaranteed hard currency cap. Expose measured usage versus estimates or unavailable provider cost.

Reaching the budget requests a pause of relevant automated work and prevents fresh dispatch/effects while preserving fulfilled outcomes, reviews and unresolved operations. Only the existing explicit owner continuation can add budget; the next cron tick or retry cannot. Continue only after previous attempts settle, uncertain effects are reconciled and current authority still allows the work.

Use existing workspace/per-bot concurrency, root handoff and depth bounds. A waiting manager releases execution capacity when its native turn settles; never start another execution on the same bot while the former native run is still active or uncertain. Account for decisions-API waits honestly rather than simply relabeling a live native run as capacity-free.

Keep failed/waiting branches out of ready candidate selection where possible, reevaluate promptly when results/capacity change, and retain periodic backend reconciliation for lost wake events. Demonstrate independent work progressing beside a waiting manager with a deliberately constrained active-slot setting. A heartbeat indicates contact, not progress; report queue reason, last actual activity and current dependency.

Use active budget, optional real owner deadline, grant expiry, start window and review expiry as distinct concepts. Do not add a five-minute wall-clock task cutoff, periodic model checkpoint or artificial yield requirement.

## 10. Questions, reviews and resumption

Reuse existing typed input requests and exact-action reviews. Each request is bound to its actual task/work/outcome revision and payload/question hash; decisions are authenticated owner actions. Saving recurring instructions or a management grant does not copy an Allow once decision to future occurrences. Existing connector standing permissions remain separately scoped and revocable.

For a genuine wait, persist it, let the runtime reach a supported settled/waiting boundary, and resume the same occurrence when the correlated result arrives. Duplicate owner decisions or simultaneous child-completion/decision events must not produce duplicate continuations. An early answer while the native turn is winding down remains attached until it can be safely delivered.

Cap applicable review/input eligibility at the relevant owner deadline and management expiry, or enforce an equivalently strict current-authority check before acceptance/dispatch. An expired/replaced/denied/removed request never becomes approval. Superseding a requirement cancels its obsolete requests; stopping work or revoking authority closes actionable cards without deleting history.

Denial records no effect and gives the manager the actual outcome. Continue other permitted work when useful, but never repeatedly prepare the same denied payload to pressure the owner. If an essential result is impossible, expose the unmet requirement. An owner can explicitly revise the goal; discussion or status questions do not do so.

The manager can wait across browser closure or backend restart. Waiting indefinitely on a required owner answer is a visible unresolved state, not success. Expiry and cancellation take their explicit paths; no fabricated default answer. New attention routing/push remains Phase 5.4.

## 11. Controls, editing and archive behavior

| Owner action | Future occurrences | Current occurrence |
|---|---|---|
| Pause schedule | Stop new automatic starts; cancel unstarted admitted work as existing 5.1 specifies | Started work continues with its saved scope and still-valid authority |
| Stop this run | Schedule remains configured unless separately paused | Cancel the owned occurrence, descendants and linked automated replies; reconcile in-flight effects |
| Pause and stop current run | Pause future scheduling atomically with recording the stop request | Same run-scoped cancellation; history remains |
| Edit routine/trigger | New saved revision applies to future/unstarted work | Existing started run keeps admitted instructions, collaborators and budget |
| Direction in a current run | Does not rewrite future saved instructions | Existing intake/controller revises the intended current work; preserves occurrence binding and prior fulfilled effects |
| Archive routine/trigger | No new occurrences; retain definition/run history | Started work follows existing behavior unless owner also selects Stop |
| Revoke standing grant | No new dependent admissions; pause affected definitions | Existing revoke-and-stop contract applies to all live work using this grant, including other routines; explain that scope before the action |
| Grant expires | No fresh authorization; pause affected definitions | Stop/cancel newly unauthorized work and settle admitted effects; never silently extend authority |
| Archive destination | Pause dependent scheduling; no silent resumption on restoration | Apply existing conversation-archive stop/review behavior; preserve links/history and do not redirect results to a different conversation |

Keep the current revoke-and-stop action. “Let the current run finish” is compatible with pausing future scheduling while its grant remains valid; it is not permission to keep executing after revocation. Do not introduce a new revoke-but-continue policy inside this milestone.

Cancellation is cooperative and idempotent. Mark stopping until actual native/lifecycle/connector settlement is known. Release neither overlap nor execution exclusivity merely because the owner clicked Stop. Requests already admitted to a provider can finish; record successful effects or uncertainty. No automatic compensating changes or deletion of partial successes.

## 12. Recovery and failure behavior

| Failure point | Recovery contract |
|---|---|
| Native wake delivered twice; concurrent workers | Use the same occurrence/owner mutation identity and transactional claims; one work root. |
| Crash before admission commits | Transaction leaves no partial admitted work; a later eligible wake may admit normally. |
| Crash after commit before native start | Resume the same queued assignment/attempt with its saved scope after revalidation. |
| Native start accepted but response lost | Reconcile the original attempt and idempotency key within supported retention. Absence of a local receipt is not absence of execution. |
| Runtime has no receipt or retention may have expired | Keep uncertain; require reconciliation, not a new native run. |
| Manager fails after child results arrive | Preserve results and bounded safe recovery behavior; any replacement/continuation uses existing evidence and scope. No whole-tree replay. |
| Child definitively fails | Wake manager, permit scoped repair/reassignment when safe, retain original failure. |
| Model/provider is unavailable or rate limited | Reuse existing bounded backoff/safe pre-tool recovery, limits and capability state. Do not continuously rerun the whole occurrence or silently change a pinned model. |
| Owner decision arrives during restart/settlement | Persist by request identity, revalidate current revision/authority and enqueue at most one continuation. |
| External write succeeds but its response is lost | Retain uncertain operation; reconcile through supported evidence before any repetition. |
| Access revoked or selected files/skills removed | Prevent affected new actions; show exact blocker. A saved input reference cannot preserve revoked access. |
| Laptop sleeps or Docker stops | No background availability claim; use bounded existing missed-run policy on return. |

Keep three-consecutive-definite-failure schedule pause from 5.1. Determine failure from the occurrence's actual required outcomes and settled automated work, not only a finished manager turn. Skipped/awaiting-owner/cancelled/uncertain work is not silently counted as a definite execution failure or success. Expired authority and archived destinations pause immediately with their own reasons. The backend can reconcile state without waking the manager merely to repeat a status message.

## 13. Incremental windows, evidence and presentation

Freeze `since_success` timestamps per admitted occurrence and pass the same interval to collaborators. Advance only after required current outcomes and contribution acceptance succeed and relevant attempts/effects are settled. Do not advance on a report shell, partially successful tree, cancellation, denial of an essential effect or uncertainty. Preserve existing half-open window semantics and the distinction between an information window and source-item deduplication.

Define compatibility for the previous-success lookup: changed instructions/results/input versions/manager/collaborator scope or management operations must not reuse an inappropriate cursor. Routine name, schedule presentation and explicit renewal of equivalent authority need not erase the data window. Record semantic compatibility rather than relying solely on a new grant ID or revision number. Manual run behavior remains explicit and consistent with 5.1, including its frozen logical time.

History links the definition/revision, occurrence, work item, accountable manager, collaborators, current required results, questions/reviews, artifacts and operations. Reuse the existing work/outcome projection for an effective run state; do not add a second mutable success flag that disagrees with the work card.

Audit identity, decisions and state transitions with safe identifiers and sanitized errors. Do not copy credentials, secure-input content or arbitrary provider payloads into logs or summary context. Per-occurrence native sessions are separate conversation contexts, not clean copies of a bot's persistent memory/workspace; record actual configuration/capabilities at execution and revalidate changes rather than promising complete reproducibility.

Distinguish scheduled time, admission, actual start, active work, waiting reason, last meaningful progress, stopping and uncertainty. The routine can be active while its current occurrence awaits review; show these as different facts. A known invalid authorization must have an actionable paused/error state. Open run navigates to its actual conversation/thread. Reconnection replays durable state without duplicating delivery or creating work.

Persist the report in the configured conversation with existing idempotent result-message identity, preserving underlying artifacts/receipts. Internal conversation persistence is delivery evidence for this milestone; an external Slack/email send is a separately requested connector result. Do not claim a network delivery guarantee from model text.

When UI implementation begins, apply the existing design-taste frontend skill. Keep the new controls inside current routine/event forms, defaults legible and advanced settings collapsible. Verify keyboard/focus, compact layout, overflow, long names, archived/deleted teammates, stale revisions, connection failures and history paging. No UI restyling is performed by this plan.

## 14. Implementation slices and affected surfaces

1. **Contract and migration:** optional typed management configuration, server validation, owner provenance and immutable run scope. Preserve old single-bot definitions/history and null defaults. Choose minimal indexed association for automated tasks only after tracing existing run/work references; do not rewrite old execution semantics or replay events. Deploy compatible migrations/backend/workers before exposing the new save controls, and drain/reconcile affected active work as required; an older worker must not silently execute a definition whose new scope it does not understand.
2. **Authority through ordinary execution:** update shared saved-work admission and effective grant checks; propagate binding through delegation, peer replies, owner revisions and replacements. Update capability discovery to match enforcement. Test invalid scope/expiry/revocation and ungranted fallbacks before connecting the UI.
3. **Execution, budget and recovery integration:** close aggregate-accounting gaps for automated replies; ensure real waits free slots after native settlement; preserve result/operation identities across restarts; integrate run-scoped stop, grant revocation and consistent locking.
4. **Owner configuration and inspection:** extend existing scheduled/event forms and grant selection, draft parsing, history/status and controls. Natural-language requests still produce inactive drafts. Keep normal conversation context selection automatic and out of configuration-heavy per-message UI.
5. **Acceptance and review:** deterministic failure/interleaving tests, browser workflows, real native scheduled/event demonstrations, affected regressions, code review and fixes, source-integrity/preservation checks and evidence documentation.

Expected backend touch points: `routine_models.py`, `routines.py`, `service.py`, `coordination.py`, `execution_policy.py`, `worker.py`, `capabilities.py`, `work_intake.py`, `work_items.py`, relevant review/revocation/controller handlers, `trigger_models.py`/`triggers.py` and an additive migration only if required. Trace all callers of changed shared checks, including manual management/lifecycle and connector paths, before editing. Keep unaffected ordinary work behavior intact.

Expected frontend touch points: `Routines.tsx`, `EventTriggers.tsx`, shared `api.ts` types, existing standing-grant navigation in `Management.tsx` and relevant work/history controls. No new workflow designer or infrastructure dependency.

Expected validation/documentation: extend `tests/test_routines.py`, `test_coordination.py`, relevant phase2/grant, execution, controller, input/review and event-trigger tests; add a focused recurring-orchestration integration/acceptance fixture and browser checks. Update fixtures/types/contracts affected by the optional field. Publish acceptance/review evidence, then update this file, `CHECKLIST.md`, roadmap, phase details and context with actual results and remaining limits. Do not mark the milestone complete based on test count alone.

## 15. Acceptance matrix

| Area | Required evidence |
|---|---|
| Compatibility | Existing scheduled/event definitions without management still admit identically; old histories, failed attempts and receipts are preserved. |
| Configuration | Correct grant manager/targets/operations; reject unknown/self/unauthorized scope, expired/revoked grants, forged actor/snapshot and stale saves. Draft alone creates no authority or running work. |
| Identity | Concurrent wakes and repeated Run now keys produce one occurrence; edit/tick interleaving does not duplicate one scheduled instant; a fresh manual run is distinct. |
| Scope | Selected subset limits a broader grant; discovery and actual operations agree; nested handoffs/messages cannot escape; a peer reply/revision/retry cannot lose the binding or acquire broader authority. |
| Contributions | Two branches produce real distinct contributions; required failures wake the manager; final acceptance requires inspection/review; optional work settles without orphan effects. |
| Inputs and access | Explicit file/attachment grants only; missing skills/revoked files/removed targets block correctly; a management grant does not expose private memory/history or another bot's credentials. |
| Capacity | With constrained active slots, waiting manager releases usable capacity after native settlement and unrelated work progresses. No busy polling, deadlock or duplicated per-bot execution. |
| Budget | Manager, parallel children, automated peer replies and retries share allowance; waits do not accrue active time; exhaustion pauses and explicit continuation revalidates. Include live accounting intervals. |
| Human decisions | Approve, deny, edit/replace, expire, remove and answer; duplicates and early decisions resume once; expired grants/stale work reject dispatch; obsolete cards cease to be actionable. |
| Authority races | Revoke/expire before first claim, between contribution completion and resume, and between approval and dispatch. Verify actual stop/admission boundaries and retained in-flight receipts. |
| Controls | Pause future versus Stop run versus combined pause/stop; routine edits, archive/restore and shared-grant revoke affect exactly their stated scope. No automatic restart on grant replacement. |
| Recovery | Restart API/worker while queued, working, awaiting contributions, awaiting review and after external admission. Preserve work/native identity, results and uncertainty; no whole-tree replay. |
| Scheduling | DST/month-end, start-window expiry, skip/latest downtime, overlap during review/budget pause/uncertainty, daily caps and definite-failure pause retain documented behavior. |
| Incremental data | One frozen window across children; verified success advances; failed/partial/uncertain work does not; semantic scope changes reset incompatible history. |
| Delivery/status | Correct conversation/thread, idempotent persisted final result, matching routine/work state, explicit queue/authorization blockers and inspectable contributions/receipts. |
| Event parity | One already-supported signed/local provider event admits a manager with the same scope/budget/review behavior; normal non-manager triggers and own-write suppression still pass. |
| Integrity | Unmodified Hermes source and preserved private volumes/configuration/connections; no connection-based native-tool blocking. |

### Real local demonstration and cleanup

Use an isolated owner-visible acceptance group and explicitly selected existing teammates. Save a manager routine with two collaborators and a bounded grant. Have each collaborator execute a small tool-backed contribution and publish evidence; require manager inspection and one combined result. Observe at least two scheduled occurrences with distinct thread/work identities, plus a separate manual run where useful.

In one occurrence, exercise a real owner question or exact review, close the browser/restart the backend while waiting, and demonstrate one continuation after the actual owner decision. Use a reviewed connector mutation only when necessary to establish external-action continuity; prepare its exact payload for owner decision using existing policy. Never simulate an owner approval in a live run. Deterministic fixtures cover denial/expiry/race failures without unnecessary external changes.

Demonstrate a supported event-triggered manager path, an unavailable collaborator, constrained capacity, grant revocation and stop behavior with controlled fixtures. Keep live provider, synthetic event and isolated policy evidence clearly labeled. Reuse configured sources where suitable; do not add a new provider or public ingress.

After evidence is recorded, stop/settle fixture work, archive test routines/triggers/group and revoke only fixture-created grants. Preserve original bots, owner-created groups, connections, sources, private credentials, volumes and historical evidence. Do not delete external test objects without authorization; list them if retained.

**Milestone exit:** the schedule-to-manager-to-two-contributors workflow, owner wait/resume, correct delivery, restart/revocation recovery, event-path parity, focused regressions and code review/fixes all pass with recorded evidence. Phase 5.4 and the overall Phase 5 exit remain separate pending work.

## 16. Limits and implementation decisions

This is comprehensive planning against the inspected code and documented failure classes, not proof that no unknown defect remains. Before implementation, settle exact scope-storage/lookup details, lock order, additive migration/index needs and the existing archive/retry hooks through deterministic tests. These are engineering choices within this plan, not reasons to start deferred features or ask the owner to design internal schemas.

Runtime/model behavior, source state and provider scopes can change. Verify capabilities against the pinned installed Hermes image rather than assuming all latest documentation applies. Local operation requires this computer and Docker to stay running/awake; no remote wake or uptime guarantee is added.

Connector controls cover Hermbot-managed operations. Independently authenticated native browser/terminal actions remain outside connector review/receipt guarantees. No design can promise exactly-once arbitrary external effects without provider support; unresolved outcomes require reconciliation. Budget enforcement has in-flight/polling margins and cannot guarantee a hard monetary cap from unavailable usage telemetry.

Aggregate accounting covers admitted Hermbot executions. Native subprocesses/subagents may not expose independent compute usage, and independently created native cron jobs are outside these application-managed occurrence records. Do not claim the run budget measures every underlying process or that it controls all native automation; retain this boundary without disabling native tools.

The research stage changed no runtime code, live configuration, grant or provider object. Subsequent implementation and focused acceptance are recorded below; the broader evaluation suite remains deferred.

## 17. Delivered implementation and acceptance — October 26, 2026

The schedule and event editors now offer **Team coordination → Coordinate teammates**. Select the accountable manager in the existing teammate field, explicitly select collaborators and choose an existing valid standing permission. The form can create that ordinary owner-issued permission inline through the existing grant API. Select the permitted subset of operations, inspect expiry and shared-budget text, then save. Creating permission alone does not save or activate a routine. The schedule preview warns when the next occurrence falls after permission expiry.

Both saved-work paths accept optional typed management settings. Migration 016 adds an immutable work-level scope and an indexed static occurrence association for automated tasks. The server freezes the selected manager/targets/operations, initiating owner/source and admission context. Each use intersects that scope with the actual current grant. Lifecycle operations cannot be selected. Single-teammate saved definitions retain their old behavior.

Delegations, repaired assignments, owner revisions and generated informational replies retain scope, routine/event identity and the original shared active-time budget. Cosmetic message reassociation cannot detach that budget or authority. Native input includes the enforced scope and frozen routine data window; explicit artifact handoffs still control file sharing. Current grant expiry/revocation is checked at dispatch and normal tool/decision boundaries. Invalid active definitions pause with an actionable reason; no replacement grant is selected automatically.

The shared budget includes manager, contributors, peer replies, subsequent attempts and their currently unaccounted live intervals. Genuine waits release usable capacity after the native attempt settles. The owner can pause future starts, stop one run or combine pause with stop. Whole-occurrence stop covers separate peer/replacement roots and queued messages. Completed effects/receipts remain; unknown effects continue to require reconciliation.

Manager completion requires accepted current required results. Merely ending a native turn with completion prose is a definite failure when required results remain missing, rather than a successful occurrence. Final acceptance also refuses unsettled optional automated work. The failure-pause decision checks settled occurrence work and current required results; a successfully repaired occurrence does not remain a definite failure merely because its first root failed. Incremental windows compare semantic instructions/results/inputs/manager/scope, permit equivalent grant renewal, and wait for accepted results plus settled automated work and effects.

| Evidence | Result and boundary |
|---|---|
| [Full backend suite](../test-results/phase53-backend.xml) | **581 passed**, including 34 new recurring-management cases. Tests use only `hermbot_test`. Existing manual management, single-bot routines/events, decisions, provider and execution checks remain in the suite. |
| [Browser proof](../test-results/phase53-browser.json) and [static UI inspection](../test-results/phase53-ui-lint.json) | Real owner permission creation/selection in the shared routine form; invalid draft cannot save; collaborator choices are explicit; operations and manager changes behave correctly. Keyboard operation, expiry warning and 1440/768/390/320px checks pass. Actual screenshots were inspected. Static warnings are existing clock formatting; no failing UI rule. Browser-created permissions are revoked afterward; the draft routine is never saved. |
| [Native demonstration](../test-results/phase53-live.json) | Two scheduled occurrences, distinct work/thread/session identities; each Sage run delegated to Scout and Quill. Both helpers used native terminal calculations, published two separate artifacts, and submitted contributions. Sage read both, accepted both and delivered 42, 110 and their sum 152 to that occurrence's conversation thread. Stored tool, artifact, acceptance and final-message receipts were checked. |
| Same native proof | One supported signed **local webhook fixture** admitted a manager with the same contract; its two contributions, inspection, accepted result and delivery also pass. This is synthetic local event parity, not a repeat of Phase 5.2's live-provider event delivery tests. No connector operations were created by these demonstrations. |
| [Restart/owner-answer proof](../test-results/phase53-restart.json) | API/worker restart and final deployment retained the exact question and settled first attempt. The authenticated owner selected Continue local test; one input-answer event resumed the same native session. The manager delegated and genuinely waited, then resumed for contribution review and accepted delivery. All three manager turns settled; the original attempt was preserved. |
| [Hermes integrity](../test-results/phase53-integrity.json) | All **2,169 upstream Python files** match the pinned official image and all three running bots. All nine separate private volumes and runtime protections are retained. Original owner bots, groups and active routine definitions were compared with the before snapshot and preserved. |
| Build and lint | TypeScript and production Vite build pass; Ruff passes for backend/new fixtures/audit. The existing bundle-size and dependency-deprecation warnings remain. |

The actual owner selected **Continue local test** on question `e4ab2053-0069-4aa5-bbd0-347ae72f77c4` in the isolated acceptance group. Its original task `44b60bfa-37b4-4639-9884-bcf2e78ab8d2` completed in the original native session. The recorded first attempt survived the prior restarts; exactly one authenticated input-answer event was recorded. Two later native turns performed ordinary continuation: delegate and wait for the two contributors, then read their artifacts and accept the combined report. Both contributions and the final report are verified. No answer was simulated, no connector operation was created and no new occurrence replaced the waiting work.

The receipt verifier now passes for **all four manager demonstrations**, eight tool-backed contributor artifacts and their accepted results. The final cleanup archived the remaining wait routine and group, revoked only the fixture grant and confirmed no fixture execution remained active. All original owner bots, groups and active routine definitions match the before snapshot. The completed scheduled/event fixtures and their private local source were already archived. Historical task/artifact/decision records remain inspectable. `python -m tests.recurring_management_acceptance verify` and `cleanup` retain the guarded evidence checks; [native proof](../test-results/phase53-live.json) records `cleanup_complete=true`. No external test objects were created by 5.3.

Review corrections and the initially failing integration check are recorded chronologically as C50–C54 in [ISSUES.md](ISSUES.md). Tests cover controlled failure, revoked/expired grants, a paused/continued aggregate budget, constrained capacity, revision/tick and revoke/claim interleavings and refusal of ungranted fallbacks. These deterministic fixtures are distinct from the actual native-model demonstrations. They do not establish arbitrary-provider exactly-once effects or every possible crash timing.

**Milestone exit:** Phase 5.3 is complete for the owner-approved local scope, including actual owner-answer continuation, fixture cleanup and recorded review fixes. **Next:** Phase 5.4 attention/notifications, then the overall Phase 5 exit. Connected-service coordination remains after all Phase 5, mobile remains Phase 6A after Phase 6, VM migration/hosting/evaluation ordering remains unchanged. This machine and Docker must stay awake/running. Existing native-tool review and compute-accounting limits in section 16 still apply.
