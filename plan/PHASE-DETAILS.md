# Expanded application phases and completion criteria

Updated: 2026-10-26.

This document expands the eight original phases and the inserted Phase 6A in the [approved roadmap](README.md) into user workflows, implementation boundaries, dependencies and acceptance criteria. Milestone numbers and completion status remain in [CHECKLIST.md](CHECKLIST.md). The expansion clarifies delivery scope; it does not start implementation, select new providers or frameworks, or reorder existing owner decisions.

## Status and sequencing

| Phase | Outcome | Recorded status |
|---|---|---|
| 1 | Real collaboration and recoverable orchestration | Complete for the recorded local scope |
| 2 | Owner-managed persistent teammates | Complete locally; Linux staging/HTTPS deferred |
| 3 | Durable files, knowledge and computer assistance | Complete for the recorded local scope |
| 4 | Connected-application collaboration | Local acceptance complete October 19 |
| 5 | Scheduled and event-driven responsibilities | 5.1 complete; 5.2 local live-provider acceptance complete October 25; 5.3 local acceptance complete October 26; 5.4 implemented with 638 tests/native acceptance; actual desktop push/cleanup and phase exit pending |
| 6 | Complete private web workflows | Pending; individual existing UI improvements are partial progress |
| 6A | Agent-operated mobile devices | Pending; after Phase 6 and before the unchanged Phase 7 |
| 7 | Dependable private release | Pending |
| 8 | Invited members and controlled sharing | Pending |

Completed phases are described to make their responsibilities and dependencies clear. Their dated acceptance reports remain the evidence for what was actually verified; the examples below are not claims of newly run tests or a fresh audit. Later defects in [ISSUES.md](ISSUES.md) remain open until individually evaluated and resolved.

Preserve these decisions throughout:

- Hermes remains the supported harness and its source remains unmodified. Use supported interfaces, configuration and extensions.
- The current runtime remains one managed container per bot. [VM isolation](VM-ISOLATION.md) is deferred until after Phases 1–8 and their exit gates; reconsider it with the owner then.
- Evaluation research is saved in [EVALUATIONS.md](EVALUATIONS.md). Implementation remains deferred; when resumed, run baseline evaluations before the existing issue-fix backlog, combine findings, prioritize fixes and rerun evaluations. Its position among feature milestones has not been selected.
- Staging/HTTPS remains deferred. Identify delivery prerequisites in later phases without silently authorizing public exposure or a remote deployment.
- Owner update, October 25: finish Phase 5 first. Agent-operated mobile use is Phase 6A, after Phase 6 and before the unchanged Phase 7; VM migration is not a prerequisite.
- Owner update, October 26: [connected-service work coordination](CONNECTED-WORK-COORDINATION.md) follows full Phase 5 completion, including 5.3, 5.4 and its exit gate. The researched shared-admission/correlation/ownership/operation-recovery solution is saved and pending; it does not add a Phase 5 gate, renumber phases or change evaluation-before-backlog-fixes ordering.
- Every future milestone requires a concrete demonstration, proportionate automated checks, review/fixes, updated documentation and checks for affected earlier behavior. An execution ending or a model saying “done” is not sufficient evidence of delivery.

## Phase 1 — Collaboration and orchestration foundations

**User outcome:** separate teammates can complete a shared assignment, exchange selected results and recover when an execution or connection fails. [Recorded acceptance](PHASE1.md).

### 1.1 — Reproducible environment

Provision the API, worker, supervisor, database, object storage and three independently identified Hermes runtimes. Each runtime receives private state, workspace and credentials. Verify the configured real model and supported native interfaces before advertising readiness.

The infrastructure must distinguish an unavailable model, an incompatible runtime, failed plugin loading and a merely booting container. Browser streaming, native approval and Stop are part of the execution contract. Acceptance demonstrates actual model/tool work rather than health endpoints alone.

### 1.2 — Common coordination tools

Provide teammate discovery, informational messages, delegated assignments, selected artifact references and durable result delivery. Store the relationship between owner request, logical work, executions and delegated contributions. A busy teammate queues an assignment without losing human instructions.

Dependencies, cancellation and execution limits belong to durable application state. A waiting coordinator releases its execution slot. Recovery reconciles the existing execution identity before dispatching replacement work; a timeout does not establish that the prior attempt never ran.

### 1.3 — Collaboration across contexts

Demonstrate both named delegation and autonomous selection of an eligible teammate. Cover research/writing, file/data processing and code/review. Run the common collaboration path from group conversation and one-shot scheduled contexts.

Share only explicitly admitted inputs and outputs. Another teammate's private browser profile, memory or working volume does not become shared because a manager requested help. The result must use the actual supplied evidence, with retrievable outputs.

### 1.4 — Manager orchestration proof

An owner grants one ordinary bot authority over two specified teammates for a concrete result. The manager assigns dependent work, inspects contributions and combines the deliverable. Exercise redirection, a recoverable failure and cancellation of the owned task tree.

The manager must remain within its grant. Reassignment reconciles the old attempt and its possible effects first. Task ownership does not authorize stopping unrelated work or administering a whole runtime.

### 1.5 — Thin web UI and recovery

Expose roster, conversation, group, work status, approvals, outputs and Stop. Reconnecting the browser restores the application view without restarting a task. Worker/runtime recreation, duplicate delivery, an interrupted handoff and a pending approval retain consistent identities and state.

**Exit:** real runtimes collaborate across the task types, management stays scoped, outputs are accessible, and work survives browser absence and supported restart/recovery. Later phases build on this contract.

## Phase 2 — Persistent teammate management

**User outcome:** the owner creates and manages teammates through the app, including authorizing a manager to request a specialist. [Recorded acceptance](PHASE2.md), [review](PHASE2-REVIEW.md).

### 2.1 — Owner onboarding

Secure owner login creates expiring, revocable sessions. Setup captures model-provider configuration, defaults, timezone and permitted capabilities without exposing stored keys in the UI. Validate setup and show actionable availability failures.

OpenAI-compatible APIs are the implemented provider path. Codex/Claude subscription authentication remains separate follow-up work; listing a provider type is not proof that its authentication flow is implemented.

### 2.2 — Bot lifecycle

Create a bot with its name, role, model, tools, skills and resource allocation. Track provisioning, readiness, failure and stopped/archive states. Editing must account for active work rather than replacing its runtime underneath it.

Cloning explicitly selects transferable state and creates independent identities and storage. Archive preserves private state; restore makes it available again. Delete explains the data removed and preserves the application records and published outputs required by the existing lifecycle contract.

### 2.3 — Conversations and groups

Provide persistent DMs and groups, attachments, threads, mentions and reactions. Let users inspect queued work, edit unstarted instructions, redirect running work, Stop and request a safe retry. Group membership and removal must account for unfinished work.

Ordinary messages use automatic interpretation. Discussion, answers, status questions, assignments and revisions must not each become separate sidebar work items. Related revisions remain associated with the right work; observed progress and waiting states remain consistent across the conversation and roster. Known interpretation defects remain in the evaluation backlog.

### 2.4 — Bot-managed lifecycle

Grant a manager specified operations over named bots, with duration and resource limits. Present an exact specialist-creation/configuration/start/stop proposal, including its implications, before any required owner decision. Apply authorized operations through the supervisor.

Exercise rejection, expiry, revocation, stale configuration, failed provisioning and partial completion. A bot cannot approve its own proposal or increase its own authority. A newly provisioned specialist joins an orchestration only after it is ready and the necessary assignment is authorized.

### 2.5 — Product shell and staging

Use the roster/conversation/context layout with responsive navigation and readable runtime states. Reconnect from a fresh browser session without losing saved work. Owner actions and long lists remain reachable at compact sizes.

Linux staging and HTTPS remain an explicit deferred part of this original milestone. Revisit deployment when the owner chooses; do not describe local acceptance as completed hosting.

**Exit:** the owner creates a bot, assigns management responsibility, reviews a new specialist and receives a collaborative result through the UI. Local completion does not establish multi-user sharing.

## Phase 3 — Files, knowledge and computer assistance

**User outcome:** work produces usable durable outputs, and the owner can inspect knowledge, review actions and assist a bot on its computer. [Recorded acceptance](PHASE3.md), [review](PHASE3-REVIEW.md).

### 3.1 — Artifacts and file library

Upload supported files or import links, retain original bytes and preview supported content. A file has provenance, a version, permissions and a reliable download. Share selected versions explicitly with conversations or tasks; distinguish the bot's private workspace from published output.

An output must satisfy its requested format and access conditions to count as delivery. Existing ZIP publication, packaging, derived-output freshness and filename findings remain open backlog items, not features made complete by this expansion. Preview limits and supported types remain documented product constraints.

### 3.2 — Memory and skills

Inspect, edit or remove saved memory; discover prior work; create/import/version/enable procedures. Saving a successful workflow produces a reviewable skill rather than silently installing arbitrary transcript content.

Select relevant enabled skills during work preparation, explain prerequisites and preserve permission boundaries. A skill is instruction content, not additional authority. Reviewed improvements and orchestration procedures must remain tied to the selected bot and version, with safe application between executions.

### 3.3 — Computer preview and takeover

Each bot has a private desktop/browser environment and persistent browser profile. Provide preview, live view, Start/Stop and explicit Take over/Hand back. Coordinate ownership with dispatch and tool admission; settle admitted calls before accepting human input.

Disconnect, reload and another owner session must not produce overlapping ownership. Describe the actual boundary accurately: the existing trusted-runtime admission design does not guarantee control over every arbitrary background process a terminal command could launch.

### 3.4 — Structured review and secure input

Present the exact proposed action or draft, allow supported edits, replace stale proposals and support approve/deny/expiry/cancellation. A manager requests owner decisions without making them on the owner's behalf.

Secure input goes through the dedicated computer path, outside chat and task records. Editing a review changes the approved identity; a superseded approval cannot execute the replacement. Current ordinary-chat secret propagation remains a separately recorded defect.

**Exit:** an orchestration uses selected files and skills, pauses for human computer assistance and returns versioned outputs with recorded decisions and no overlapping control.

## Phase 4 — Connected-application collaboration

**User outcome:** authorized teammates combine Google, Slack, GitHub and supported MCP access while preserving exact action identity and external receipts. [Current completion record](PHASE4.md), [review](PHASE4-REVIEW.md).

### 4.1 — Connection lifecycle

Connect an account privately, select eligible bots, discover available actions and handle refresh/reconnect/revoke. Connector credentials remain encrypted behind backend tools. Supported MCP connections use the documented transport and endpoint requirements; a local stdio device server is not automatically compatible.

Connection revision and current access are checked when acting. Invalid input, denied resources, missing scopes, authentication loss, provider outages and uncertain effects should be distinguishable. The recorded diagnostic/tool-contract gaps remain evaluation cases even where owner-assisted provider acceptance succeeded.

### 4.2 — Google workflows

Read/search Gmail, inspect a parent message before drafting an actual threaded reply, review/edit/send, and verify the resulting provider message and thread. Calendar workflows inspect availability and prepare authorized event changes. Drive workflows search/read/import selected content and create authorized files/documents.

Use actual provider IDs and stored headers where relevant. A successful draft is not delivery evidence. A lost response or rewritten Message-ID cannot be treated as proof of absence followed by an automatic resend. Live and isolated coverage remain distinguished in the completion record.

### 4.3 — Slack and GitHub workflows

Find permitted Slack resources, read context and prepare a message for the intended channel or person. Resolve targets through supported identities, then retain an operation receipt for posting. For GitHub, inspect permitted repositories/issues/PRs and prepare supported changes.

Delegate to a bot that has the necessary connection without copying its credentials. Confirm recipients and destinations against the owner request. The local GitHub acceptance proves the recorded issue creation/readback; it does not imply live acceptance of every possible PR mutation.

### 4.4 — Common policy and orchestration

Evaluate connector changes against the current owner request, workspace policy and any exact approval or scoped standing permission. Revision, revocation, changed payloads and expired grants invalidate inappropriate dispatch. Management authority does not expand app access.

Keep successful provider receipts even if later reporting or artifact work fails. Resolve uncertain operations through evidence before any replay. Connections preserve owner-enabled native tools; independently authenticated browser/terminal actions retain their documented coverage limit.

**Exit:** the recorded real Google/Slack/GitHub orchestrations complete, exact denial produces no effect, reviews and receipts correlate, and recovery preserves uncertain/rejected history without replay. Broader quality defects remain governed by evaluations.

## Phase 5 — Ongoing responsibilities and background management

**User outcome:** saved responsibilities respond to time or supported events, coordinate teammates and bring results or necessary decisions back to the owner without requiring an open browser.

### 5.1 — Routines

**Complete within the local scope:** [implementation and acceptance](PHASE5-ROUTINES.md). The owner edits an inactive chat-proposed draft or creates a routine directly, selects the teammate/conversation/inputs/results and previews the schedule in its timezone. Provide Run now, pause/resume, delete and retained history.

Reuse supported native Hermes scheduled jobs to signal wake-up; the application owns due-run admission, revision binding and ordinary authorization. Preserve defined overlap, missed-run, start-window, failure-pause and active-budget behavior. Each occurrence has its own thread and work item. Browser absence is supported; sleeping hardware is not an always-on service.

The “since last successful run” window is source context, not source-item deduplication or permission for a new external effect. Existing local arithmetic acceptance and isolated connector-policy tests do not establish live routine writes for every provider.

### 5.2 — Event triggers

**Local acceptance complete October 25:** [behavior, setup, evidence and limits](PHASE5-EVENTS.md), [review and fixes](PHASE5-EVENTS-REVIEW.md). The signed-event/Hermes/restart proof and live Slack, Drive, Calendar, Gmail and GitHub event/own-write checks pass. Public ingress is not deployed. Coordination across overlapping handlers and related cross-service input is a separate [post-Phase 5 follow-up](CONNECTED-WORK-COORDINATION.md).

The owner configures a supported event source, filter, destination, responsible teammate and instructions. An incoming event supplies facts; saved owner configuration supplies authority. Provider payloads cannot rewrite the instruction or grant themselves access.

Persist source identity, delivery identity and occurrence before dispatch. Authenticate events, bound their input, deduplicate provider redelivery and prevent Hermbot-created effects from repeatedly triggering themselves. Recheck connection and trigger revisions at admission. Disable/archive/revoke must prevent new dispatch while retaining an inspectable event history.

Google change detection needs durable cursors and explicit behavior for expired subscriptions, missed changes or resynchronization. A cursor must not advance past unrecorded input. Coalescing or ordering behavior must be defined per supported event type rather than assuming arrival order is reliable.

**Local-operation gap to resolve:** provider webhooks need a reachable delivery path, while deployment is deferred. Choose supported polling, a suitable authenticated provider transport, or separately approved ingress for each source before claiming live acceptance. Local signed-event tests establish application behavior but not provider delivery.

**Demonstration:** one configured event produces the intended work once despite redelivery; an app-generated response does not create a loop. Show recovery after worker restart and an honest disconnected-source state. External mutations retain existing authorization and review requirements.

### 5.3 — Recurring orchestration

[Research, implementation, controls and acceptance](PHASE5-ORCHESTRATION.md), recorded October 26; [review/corrections](PHASE5-ORCHESTRATION-REVIEW.md). Local acceptance passed with 581 backend tests, responsive form verification, two real scheduled manager occurrences, one signed-local-event occurrence and one actual owner-answer continuation after restart. The final question-fixture cleanup and preservation checks passed; the milestone is complete. It reuses current Hermes cron wake-up, work, contribution, review and connector records. The broader connected-service coordination work still follows full Phase 5 completion.

Extend the responsibility definition to a manager and specified eligible teammates, expected results, shared inputs, budget and a valid standing management grant. A schedule alone must not create recurring management authority.

Each occurrence uses its own work/thread/executions and retains the initiating owner context. Delegations, waiting contributions, reviews and outcome acceptance reuse existing mechanisms. Recheck authority before new assignments; a configuration revision does not silently alter an in-flight occurrence's instruction.

Bind the run to an immutable selected subset of the current standing grant; carry effective authority and aggregate budget through automated peer responses, replacement roots, owner corrections and continuations. Validate overlap/occurrence identity during concurrent edits and wakes, preserve real waits without busy polling and exercise the same optional manager binding through existing event-trigger admission. Ordinary definitions without management retain their behavior. Initial recurring operations coordinate existing teammates; lifecycle automation is not added by this milestone.

Pause/resume and revocation must distinguish stopping future starts from stopping an already running owned tree. If the owner chooses cancellation, settle relevant work before cleanup. Preserve successful external receipts through approval delays, restarts and revoked access; do not replay prior effects as a new occurrence.

**Demonstration:** a scheduled manager coordinates two bots, waits for an owner decision, resumes once and delivers to the configured conversation. Repeat with restart, duplicate wake-up, an unavailable teammate and grant revocation.

### 5.4 — Attention and notifications

[Detailed researched plan](PHASE5-ATTENTION.md), October 26, 2026, records existing-code integration, primary-source comparisons, 2026 notification failure reports, owner flows, state/queue/delivery contracts, failure tests and acceptance. Implementation has not started; existing scope and phase ordering remain unchanged.

Create an inbox for unread results, questions, reviews, failures and unresolved outcomes. Each entry links to its actual work and current actionable state. Marking it read does not approve a proposal or answer a question. Resolved items must not remain presented as awaiting action.

Add browser push, per-category preferences, quiet hours and delivery deduplication. The in-app inbox remains authoritative if push permission is denied or a subscription expires. Notification content should reveal only what its recipient may see; define previews and device subscription revocation.

Manager summaries link to the tasks, outputs and decisions they summarize. Escalations should identify what requires attention without flooding the owner with every internal transition. Verify timezones, multiple browser sessions and stale notification clicks against current state.

**Exit:** scheduled and event-triggered orchestrations run without the browser, preserve identity through restart/redelivery, survive approval delays and present useful results or questions. Provider delivery paths and the computer-awake requirement are explicit.

## Phase 6 — Complete the private web experience

**User outcome:** everyday private workflows can be completed from the web app, including voice, taught procedures, capability setup and usable navigation. Existing font/button/sidebar fixes are progress, not completion of the whole phase.

### 6.1 — Voice

Owner hold, October 27, 2026: keep this scope pending and proceed with 6.2 without renumbering.

Start with dictation, an editable transcript and explicit submission through the existing message-intake path. Preserve the intended conversation, attachments and permissions. Handle permission denial, unavailable audio services and partial transcription without silently submitting an incomplete request.

Add spoken replies and voice memos with deliberate recording/storage controls, followed by live conversation and interruption. Distinguish stopping audio playback from stopping actual bot work. Reconnection and repeated final transcript events must not create duplicate messages or actions.

Voice can request collaboration and management through the same authorization paths as typed messages. Select speech services, retention behavior, latency goals and cost limits before implementation; none is chosen by this plan expansion.

**Demonstration:** dictate and edit an instruction, obtain a spoken result, interrupt a live exchange and inspect its durable record. A voice-requested external change follows the same applicable decision process as its typed equivalent.

### 6.2 — Demonstration teaching (removed November 10, 2026)

**Removed November 10, 2026.** The owner removed demonstration Teaching (code, UI, tables and recordings) to simplify private computer takeover. This document is kept as history; skills Teaching published remain ordinary installed skills.

[Researched implementation plan](PHASE6-TEACHING.md), October 27, 2026. The owner selected this milestone next while voice is held. The detailed capture, privacy, draft/test/publication, lifecycle and acceptance contracts below that link have a local implementation; [verification and remaining owner acceptance](PHASE6-TEACHING-REVIEW.md) are recorded.

The owner explicitly starts a recording session on a bot's computer, demonstrates a workflow, pauses where needed and stops recording. Capture actions and enough permitted context to explain them. Define secret-entry exclusion across action text, accessibility data, screenshots and video rather than only masking one input field.

Generate an editable skill draft describing prerequisites, parameters, actions, expected results and failure handling. Replace private sample values with explicit parameters. The owner reviews, tests and approves a version before enabling it. A test that changes an external service still requires the appropriate authority.

Support procedures that delegate selected work to permitted teammates. The saved procedure cannot carry the demonstrator's credentials or confer management/app access. Changes need version history and a way to return to the prior usable procedure.

**Demonstration:** teach a small workflow, inspect/edit the generated draft, test it on fresh inputs, save a version and execute it. Show a missing prerequisite and verify a secure-input canary is absent from recorded/draft evidence.

### 6.3 — Capability discovery

Provide a curated catalog of bot templates, skills, connectors and trusted plugins. Show what each does, its version, prerequisites and requested permissions. Distinguish “discovered,” “installed,” “configured” and “ready” so a listing is not confused with usable access.

Support explicit installation/configuration/update/enable/disable/removal, scoped to the selected bot or workspace. Reuse current skill, connection and lifecycle mechanisms. Handle failed installs, stale versions, missing credentials and safe application between runs. Removal must identify dependent routines or skills rather than leaving unexplained failures.

Include manager-role templates and reusable orchestration procedures. Native tool availability remains independent of merely connecting an app. Catalog adoption does not require a public plugin marketplace or additional harness support.

**Demonstration:** discover and configure a capability, complete its prerequisite checks, execute a representative task, then disable/remove it with clear handling of dependent work.

### 6.4 — Navigation, settings and consistent states

Add global authorized search across supported conversations, work and files, with results linked to the correct object/version. Provide a command palette and discoverable shortcuts. Existing per-list search and prior-work lookup do not alone satisfy this global workflow.

Complete owner settings for usage, budgets, notifications and supported export. Distinguish measured usage from estimates or unavailable provider data. Preserve existing themes and responsive behavior, then review the entire set of forms, menus, dialogs and work states for readability, keyboard access, focus and error recovery.

Inspect idle, provisioning, queued, working, waiting, blocked, failed, completed, offline and reconnecting states. Roster and work views must agree on underlying state. Handle empty workspaces and large histories, long titles, removed work, touch interaction and loading failures. Include inspection of management proposals and safe cancellation at each scope.

**Demonstration:** complete ordinary setup, conversation, group, review, file, routine and management workflows using keyboard and compact browser layouts. Resolve an injected loading error without losing an edited draft.

**Exit:** the private-web feature checklist, visual review, accessibility checks and voice acceptance pass. Agent-operated mobile use follows in the separate Phase 6A.

## Phase 6A — Mobile device use

**Placement:** owner-selected on October 25, 2026, after Phase 6 and before Phase 7. This is a separate stage; Phase 7's scope and numbering remain unchanged. Finish Phase 5 first. All mobile milestones are pending, and VM-per-bot migration remains deferred.

Mobile use means a bot operating apps on an assigned device. It is distinct from opening Hermbot's website on a phone or building a native Hermbot mobile client. Begin with Android; specific runtime/device selection and any later iOS support require scope validation before implementation.

### 6A.1 — Device lifecycle and assignment

Provision or attach supported Android devices, explicitly assign them to bots and preserve private installed apps, accounts and data. Surface device readiness, unavailable hardware, start/stop and resource limits. Archive/delete behavior must account for retained private device state and unfinished work.

Select native host-managed or companion-container placement against the actual machine and supported runtime. The host service must authenticate callers and enforce bot-to-device assignment. Device deployment does not depend on moving bots to VMs.

### 6A.2 — Agent observation and actions

Use supported Hermes tools/extensions for app launch, accessibility-tree and screenshot observation, touch gestures, text and navigation. Preserve current device/target identity and stale-target checks; screenshot images must reach the model as usable evidence.

Distinguish input dispatch from observed app success. An uncertain action requires observation/reconciliation before repetition. Attach useful device evidence and failures to the current work item. Device access does not expand management or connected-app authority.

### 6A.3 — Live view and owner assistance

Provide a live device screen with enforced agent/human ownership, explicit Take over/Hand back and a clear unavailable state. Settle admitted actions before enabling human input, and retain human ownership through viewer disconnects. Agentsims' browser viewer claim alone is insufficient because its CLI/MCP paths can coexist with browser input.

Provide owner-assisted login through secure input, with defined treatment of screenshots, accessibility data, video and traces. Avoid placing credentials into ordinary chat or recorded tool arguments. Do not claim stronger control over arbitrary native background activity than the implementation enforces.

### 6A.4 — Workflow and recovery acceptance

Complete an actual app task on the assigned device with evidence of its requested result. Verify persistent sessions across supported recreation, stale-target rejection, device/runtime disconnection, restart and reconnect. Use the existing permission and external-effect rules when a workflow can change an account.

Verify selected integration with ordinary work and routines, including unavailable devices and capacity limits. Record platform/app limitations and device-specific recovery evidence in this stage. Phase 7 retains its original reliability scope and numbering; Phase 8's existing authorization requirements apply to the new interaction path when sharing is implemented.

**Exit:** a bot completes a real task on an assigned Android device, returns inspectable evidence, supports owner assistance and preserves private state through supported recovery. Review and appropriate affected regressions pass. This is not a claim of arbitrary app compatibility or a live device already validated.

### Research and implementation inputs

Agentsims is the researched reference, not a selected installed dependency. Its local stdio MCP transport does not directly match the current public-HTTPS connector. Validate the bridge and evidence path without changing Hermes source.

The recorded source review used Agentsims commit `5ce17a621d7269ca8bd527fba65d6ca5299cb5db`: [device workflow](https://github.com/Maniktherana/agentsims/blob/5ce17a621d7269ca8bd527fba65d6ca5299cb5db/skills/agentsims/SKILL.md), [platform requirements](https://github.com/Maniktherana/agentsims/blob/5ce17a621d7269ca8bd527fba65d6ca5299cb5db/docs/installation.md), [MCP transport](https://github.com/Maniktherana/agentsims/blob/5ce17a621d7269ca8bd527fba65d6ca5299cb5db/packages/agentsims/src/server/mcp/stdio.ts), [viewer ownership](https://github.com/Maniktherana/agentsims/blob/5ce17a621d7269ca8bd527fba65d6ca5299cb5db/packages/agentsims/src/core/tools/viewers.ts) and [trace serialization](https://github.com/Maniktherana/agentsims/blob/5ce17a621d7269ca8bd527fba65d6ca5299cb5db/packages/agentsims/src/core/tools/traces/trace-file.ts). Recheck version-specific behavior when implementation is scoped.

## Phase 7 — Dependable private release

**User outcome:** the owner can rely on the application daily and recover its data and ongoing responsibilities after failure.

### 7.1 — Recovery and maintenance

Back up PostgreSQL, object storage and private runtime/workspace/browser state with a defined consistency procedure. Preserve the encryption material needed to recover saved credentials through a protected backup path. Select recovery point/time objectives before implementation.

Restore onto a clean host and verify conversations, artifacts, versions, skills, routines, grants and orchestration records together. Reconcile in-flight native executions and external effects; restoring old local state must not authorize replay of a delivered action. Document reauthentication where remote sessions cannot be restored.

Provide graceful drain, supported upgrades, schema migrations, compatibility checks and recovery controls. Test failed upgrades and version incompatibility. This is a backup/recovery plan for the current container architecture, not approval for VM migration.

**Demonstration:** perform a clean-host restore from an actual backup, download known files, inspect reconstructed work and resume a supported responsibility without duplicating an external effect.

### 7.2 — Operational visibility and limits

Correlate owner requests, work items, executions, bot containers, reviews and connector operations through safe support identifiers. Diagnose queue age, observed activity, approval waits, container health, storage growth and unavailable providers. Heartbeats must remain distinct from task progress.

Show degraded components and actionable recovery in the UI. Preserve useful sanitized error detail without logging chat credentials, keys or arbitrary provider bodies. Expose and enforce resource/active-work limits; spending data needs explicit handling when a provider cannot report reliable cost.

Use the original release target of ten registered bots and three active tasks as a test workload, subject to measured machine capacity. A large roster does not require all desktops to run concurrently. Define what the UI reports when capacity is insufficient.

**Demonstration:** inject provider failure, an unavailable runtime, storage pressure and a long queue; distinguish each state, enforce configured limits and recover without losing provenance.

### 7.3 — Release acceptance and operating documentation

Run an agreed-duration unattended exercise across routines, events, management and approvals. Choose the duration, workload, success thresholds and allowed operator interventions explicitly; do not call a short demonstration a soak test.

Exercise provider outages, API/worker/runtime restarts, connection revocation, storage/database recovery, export and deletion. Use isolated failure tests and clearly authorized live acceptance; preserve failed evidence and investigate unexplained failures. Resolve release-blocking issues using the evaluation/baseline process rather than calling historical phase completion sufficient.

Complete setup, deployment, backup, restore, upgrade and incident runbooks. The original exit calls for a deployed private application. Because Linux staging/HTTPS remains deferred, revisit that deployment scope with the owner before claiming the release gate is satisfied; do not silently replace it with local health checks.

**Exit:** the private application passes agreed cumulative reliability and recovery acceptance. Phase 8 sharing begins only afterward. Backup files existing is insufficient; verified clean-host restoration is required.

## Phase 8 — Invited teammates and controlled sharing

**User outcome:** invited people use shared bots and work without exposing private histories, files, sessions or credentials. Existing single-owner authentication does not establish any of these guarantees.

### 8.1 — Membership and access

Add invitation acceptance, owner/admin/member roles, member sessions and explicit revocation. Define permissions for every role before exposing administrative controls. Private bots and DMs remain private; group and artifact access is deliberately granted.

Authorize API responses, searches, downloads, live event streams, computer views and notifications, not only navigation links. Revoking membership must invalidate active access and define what happens to queued/running work and outstanding approvals.

**Demonstration:** two members see only their permitted resources; access revocation closes further reads, streams and actions, including direct requests using previously known IDs.

### 8.2 — Team bots

Publish a new team copy from selected role/configuration, skills, memories and files while preserving the original private bot. Show exactly what is being copied; do not silently copy browser logins, personal credentials or private conversation history.

Keep one owning persistent runtime per team bot, with explicit separation of shared knowledge from member-specific context. Define update ownership, simultaneous-use queuing, editable shared memory and archive/delete behavior without racing active work.

**Demonstration:** publish a selected team copy, use it from two authorized accounts and verify the original private bot and excluded state remain private.

### 8.3 — Member execution isolation

Carry the initiating member through intake, native sessions, delegated work, files, credentials and browser context. Personal connections follow the requester; a shared account requires explicit configuration and access rules.

Session IDs and prompt instructions are insufficient isolation when an agent can read arbitrary files in a shared runtime. Before implementation, select an OS-enforced approach that prevents commands from inspecting other members' workspaces, credentials and private session storage while retaining one owning team runtime. Validate native terminal/browser/skill paths as well as application tools.

The exact enforcement design is unresolved. This requirement is a Phase 8 gate, not an excuse to import the deferred VM-per-bot migration or claim that VM-per-bot alone isolates members inside one shared team bot.

**Demonstration:** adversarial cross-member reads and credential/session access fail through actual native tool paths. Member removal during execution cannot turn a shared runtime into access to private retained copies.

### 8.4 — Shared orchestration

Compute a manager's authority from the initiating member's actual rights. Enroll private bots only with their owner's authorization. Cross-bot handoffs carry only admitted inputs; shared groups expose permitted progress and outputs without private runtime history.

Apply member authorization to routines, triggers, reviews, artifacts, search, computers and notifications. Add explicitly configured team-bot Slack access and attribute actions to the appropriate requester/shared account. Handle concurrent managers, grant revocation, member removal and partial results while preserving successful provider receipts.

**Demonstration:** a member starts an authorized shared orchestration, another sees permitted progress, private inputs remain hidden, and revocation stops new unauthorized dispatch without replaying effects or exposing state.

**Exit:** invited members complete real shared workflows with verified boundaries across all existing interaction paths. Unresolved execution isolation blocks shared release even if collaboration appears functional in ordinary use.

## Discussion decisions before implementation

Mobile placement is settled as Phase 6A. Validate its first-device scope before implementation. The event-delivery path for local operation, voice provider choices, release recovery/soak targets and member execution-isolation design must be resolved before their dependent implementation. They need not all be decided before continuing unrelated work.

No dates or effort estimates are assigned without scoping the next milestone. Preserve existing phase evidence and the owner-selected sequence: Phase 5, Phase 6, Phase 6A, Phase 7, Phase 8.
