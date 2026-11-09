# Hermbot: functional roadmap from POC to complete web application

## 1. Summary and fixed decisions

Owner request, November 2, 2026: implement direct Codex subscription authentication alongside API keys to unblock Phase 6.2. The [current implementation, researched references, review and remaining real-account gates](CHATGPT-SUBSCRIPTION-AUTH-REVIEW.md#direct-codex-connection) supersede the earlier new-application route. Authentication belongs to Hermbot; Hermes remains unchanged. No existing provider or bot assignment is switched automatically. Roadmap numbering is unchanged.

Build a private web application with Grok Bot–style interactions and appearance, powered by independent Hermes runtimes.

Users can create teammates, assign responsibilities, and request that one bot coordinate or manage other bots.

Established decisions:

- **Hermes remains the sole supported harness.** The owner deferred harness independence, alternative harness selection and the generalized adapter platform on October 11, 2026.
- **Underlying Hermes source must remain unmodified.** Use existing configuration, public APIs and supported extension interfaces. Deferring harness independence does not authorize retaining or adding core source patches.
- **One bot currently owns one container and one persistent Hermes runtime.** The [VM-per-bot proposal](VM-ISOLATION.md) is deferred until after the existing roadmap is complete.
- Bots collaborate through explicit instructions and autonomous delegation.
- Collaboration works across chats, groups, routines, and connected-app tasks.
- Any bot can become a manager when the user grants it authority over specified bots.
- Manager capabilities include task orchestration and approved bot creation, configuration, and runtime start/stop.
- Begin with private bots for one owner. Add invited teammates and sharing after the private application is complete.
- Build the web app now; desktop remains a future option.
- Run the POC locally in Docker, then deploy the same architecture to an always-on Linux server.
- The owner manages model providers; bots may use different permitted models.
- External changes through Hermbot connectors require approval unless covered by explicit standing permission. Independently authenticated native browser/terminal actions are outside connector approval coverage.
- Include Gmail, Calendar, Drive, Slack, GitHub, configurable MCP connections, and browser workflows.
- Follow Grok Bot’s layout, interaction patterns, density, and visual style using Hermbot branding.

Development tooling runs in containers. Current implementation and acceptance status are recorded in [CHECKLIST.md](CHECKLIST.md). Local Phase 4 acceptance and final review completed on October 19, 2026; [completion evidence and limits](PHASE4.md) record the live provider cases, 447 passing backend tests and unchanged Hermes source. Phase 5.1 routines now pass local acceptance; [implementation and limits](PHASE5-ROUTINES.md) are recorded separately. Phase 5.2 event triggers also passed local acceptance on October 25, 2026, with all five live providers, review corrections and 547 passing backend tests; see [evidence and limits](PHASE5-EVENTS.md). Phase 5.3 passed local acceptance with 581 tests, four verified native manager demonstrations, actual owner-answer continuation after restart and final fixture cleanup. [Evidence and limits](PHASE5-ORCHESTRATION.md). Phase 5.4 and the Phase 5 exit passed local acceptance on October 26, 2026: 638 backend tests; fresh server-initiated native scheduled/signed-local-event manager runs; duplicate-event and active-attempt restart recovery; revalidated actual owner-answer continuation; actual owner-confirmed closed-tab exact-result notification; and final guarded fixture cleanup ([evidence/review](PHASE5-ATTENTION-REVIEW.md#phase-5-composed-exit--october-26-2026)). All original owner groups, bot configuration, active routine and browser registration/preferences remain preserved. Remote hosting remains deferred.

November 3, 2026: the owner-authorized conversation-ordering correction is implemented and deployed locally: one commit-ordered conversation timeline, retained-history pagination, attempt-scoped response identity, durable question/review history and thread-scoped owner answers. All 749 backend tests pass; the deployed read-only audit covers all 950 messages in 130 conversations and 79 thread scopes. Existing message/execution history, bot configuration and teaching evidence remain preserved, with no Hermes change or external task replay. See [C76–C77 and evidence](ISSUES.md#c76--conversation-chronology-mixed-messages-with-unanchored-runtime-footers). This does not start unrelated backlog fixes or evaluations.

[Evaluation research](EVALUATIONS.md) records the suite comparison, recommendations and limitations. Owner update, October 19, 2026: defer evaluation implementation until after other tasks. No suite has been selected or adopted; this is not the immediate next implementation step.

Owner update, October 26, 2026: implement [connected-service work coordination](CONNECTED-WORK-COORDINATION.md) **after Phase 5 is completely finished**, including 5.3, 5.4 and the phase exit gate. The saved research and solution cover shared admission for overlapping handlers, automatic correlation to existing work, one accountable teammate per request/purpose, operation-level idempotency, handovers and cross-service loop prevention. Phase 5 is now closed for the local scope. Owner update, October 27, 2026: defer this follow-up until later, with no new start date. The subsequent October 27 owner decision holds Phase 6.1 voice and selects Phase 6.2 demonstration teaching next; [the researched plan](PHASE6-TEACHING.md) is implemented locally, with [real owner acceptance pending](PHASE6-TEACHING-REVIEW.md). The saved coordination work applies across services and preserves existing phase numbering, Hermes source integrity and the evaluation-before-backlog-fixes decision.

Owner decision, October 24, 2026: preserve the [VM isolation research and proposal](VM-ISOLATION.md) for reconsideration after completing Phases 1–8 and their acceptance gates. It does not start a migration or reorder current milestones. Guest privilege policy and VM technology remain undecided; revisit with the owner before implementation. This deferral applies to VM isolation, not the separately requested mobile-use capability.

Owner sequencing decision, October 19, 2026: **baseline evaluations precede the current issue-fix backlog**. Combine evaluation findings with the existing issue register, prioritize fixes, then rerun evaluations. The evaluation deferral does not move the fix backlog ahead of that baseline; the intervening tasks remain unspecified. The owner subsequently authorized C36 alone as an exception; its receipt-verifier correction is implemented and verified. This does not authorize other backlog fixes or start evaluations.

[ISSUES.md](ISSUES.md) is the canonical chronological issue history, detailed incident register and maintenance guide. [CHAT-COVERAGE.md](CHAT-COVERAGE.md) verifies original user reports and their following diagnoses against the source transcripts. Investigations and fixes should update the chronological entry and relevant detailed record in that file with cause confidence, selected approach, actual implementation, verification and remaining limits. Prior phase completion is not proof that later defects are solved.

The implemented [work outcomes and human requests plan](WORK-OUTCOMES.md) addresses tool target guidance, typed answers, collaboration, evidence, and completion status exposed by the failed Slack DM. It preserves automatic message association and unmodified Hermes. Local acceptance, review fixes and practical limits are recorded in [WORK-OUTCOMES-REVIEW.md](WORK-OUTCOMES-REVIEW.md).

**Phase rule:** every phase delivers usable functionality, has independently verifiable milestones, preserves earlier acceptance tests, and closes only after its exit criteria pass.

## 2. Architecture, interfaces, and management behavior

### Harness independent application boundary — deferred

Owner update, October 11, 2026: discard this idea for now and continue with Hermes. The design below is retained as deferred research, not an active requirement to build a universal contract, additional adapters or mixed-harness execution. The requirement to keep Hermes itself unmodified remains active. The owner subsequently authorized removing connection-based native tool blocking. This implementation replaces all four source patches through supported interfaces and permits public browsing, commands and file editing alongside connected applications. See [PHASE4.md](PHASE4.md) for the current permission boundary.

Owner amendment, October 10, 2026: application APIs, coordination, permissions and UI consume one versioned runtime contract. Harness-specific execution payloads, session identities, event formats, provider setup, knowledge formats and control semantics belong in independently deployable adapter packages. Implement the Hermes adapter first; this decision does not authorize building other integrations now.

Each adapter declares its harness/version, configuration schema, resource requirements, supported controls, native tools and knowledge features. Advertised capabilities must be verified against the deployed version. Missing features are explicit; a declaration never grants access. The application computes effective capabilities from adapter support, configured shared services, owner permissions and task grants. It must not branch on harness names in feature code or assume all harnesses implement Hermes's memory, skills, scheduling or desktop behavior.

Hermbot owns its shared coordination, artifacts, connector authorization and reviewed-action services. Expose these through supported plugins or MCP where available. ACP can carry agent/client communication where the harness supports it; it does not replace capability verification or the application runtime contract. Adapters normalize lifecycle evidence without turning an accepted prompt, cancellation acknowledgement or disconnected stream into proof of completion. Ambiguous effects remain unresolved rather than being replayed automatically.

Selecting a lighter installed harness should be a normal teammate setting. Provision only its required components; browser/desktop workers are optional and started when needed. Switching occurs after active work settles, preserves application records and explicitly shares selected inputs; native sessions, credentials and internal memory are not assumed portable. Different agent loops may produce different results even with the same model and shared tools.

The runtime build now leaves upstream Hermes source unchanged. The previous four patches have been removed: existing `llm_request` middleware projects provider tool schemas, `tool_execution` middleware consults raw skills, and the owner skill catalog uses Hermbot's private adapter. Official-image source comparison and running-runtime checks are recorded in `test-results/native-tools-source.json` and `test-results/native-tools-audit.json`. A generalized adapter refactor remains deferred. Earlier acceptance files remain historical evidence of their respective builds.

### Application components

| Component | Responsibility |
|---|---|
| React/TypeScript web app | Roster, chats, groups, orchestration, approvals, computer views, routines, library, settings |
| Python/FastAPI backend | Authentication, permissions, bot registry, management grants, connections, product APIs |
| Background worker | Durable dispatch, group coordination, delegation continuations, orchestration, recovery, notifications |
| PostgreSQL | Product metadata, task relationships, grants, queues, approvals, durable UI events |
| S3-compatible storage | Files, artifacts, previews, recordings, exports |
| Container supervisor | Provisioning, health, resource limits, lifecycle operations |
| Hermes bot containers | Execution, bot memory, skills, sessions, tools, scheduled jobs |
| Hermbot Hermes plugin | Collaboration, orchestration, management proposals, and artifact tools |

Use Docker Compose for development and initial single-server deployment. Use PostgreSQL-backed queues initially.

The web app uses Vite, React, Tailwind, accessible Radix components, and TanStack Query. Pin infrastructure images, Hermes revisions, dependencies, and plugin versions.

Each bot receives separate persistent Hermes data, workspace, browser state, and resource limits. Only the supervisor manages containers; agents cannot access its Docker socket or another bot’s private state.

Hermes owns native execution history and agent state. The application owns product metadata, task coordination, group transcripts, and rebuildable search projections.

Hermes supports full-runtime Docker deployment and APIs for execution control. [Docker setup](https://hermes-agent.nousresearch.com/docs/user-guide/docker), [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)

### Common collaboration path

Use a Hermbot plugin connected to the backend coordination service for all application-managed communication.

This makes collaboration available beyond canonical Bot Chats, where Hermes’s native messaging tool has session restrictions. [Hermes Bot Mode](https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode)

Plugin capabilities:

- Discover permitted teammates and their capabilities.
- Send informational messages.
- Delegate tasks, inspect progress, and return results.
- Publish and reference artifacts.
- Request human input or approval.
- Create orchestration plans and propose authorized lifecycle operations.

Use authenticated Hermes runs for execution, steering, stopping, approvals, and progress. Verify runtime capabilities at startup.

Delegation remains asynchronous. Waiting parent tasks release execution slots. The worker delivers child results and resumes the appropriate parent continuation.

Logical tasks and execution runs are separate: a run may finish while its task awaits a teammate or human decision.

### User-authorized management and orchestration

A manager is an ordinary bot with a scoped management grant.

| Capability | Behavior |
|---|---|
| Plan work | Break the user’s requested outcome into tasks, dependencies, and deliverables |
| Assign work | Select permitted bots according to their roles and capabilities |
| Monitor | Inspect progress, results, failures, budgets, and blockers |
| Redirect | Steer its assigned tasks or revise pending assignments |
| Recover | Reassign recoverable work after reconciling the previous attempt |
| Finish | Combine outputs and report results and unresolved issues |
| Create bots | Propose a role, model, capabilities, and resources for user approval |
| Configure bots | Propose changes to specified bots; apply approved changes safely |
| Control runtimes | Start/stop authorized runtimes with approval or standing permission |

A management grant identifies the manager, target bots, allowed operations, resource budget, and duration. Default grants last for the requested task. Ongoing management requires an explicit standing grant.

Rules:

- Managers cannot grant themselves additional authority.
- A management relationship does not reveal private conversations, credentials, or memory.
- Managers can steer or cancel work they own through their orchestration tree.
- Stopping an entire runtime requires separate authority because it may affect unrelated work.
- Configuration changes apply between runs; active work must be drained or explicitly interrupted first.
- Reassignment must reconcile the old attempt before launching replacement work.
- Bot deletion, access-policy changes, and credential administration remain owner actions.
- Every management operation is attributed to its initiating user and recorded.
- Users can revoke management authority and stop an orchestration from the UI.

Example:

> “Manage Researcher, Writer, and Reviewer for this report. Assign the work, check their results, and bring me the final document.”

The manager coordinates existing bots immediately within the request’s scope. If another specialist is needed, it proposes creating one and waits for approval.

### Public interfaces and UI

Build versioned `/api/v1` interfaces for:

- Bot lifecycle and configuration.
- Conversations, messages, attachments, threads, and reactions.
- Groups and participant activity.
- Tasks, delegation trees, steering, cancellation, and results.
- Orchestrations, management grants, progress, and revocation.
- Management proposals and approved operations.
- Approvals, artifacts, connections, skills, routines, notifications, and settings.

Use SSE for durable application events and WebSockets for computer and voice transport.

The conversation shows a compact orchestration card: manager, participating bots, task progress, blockers, proposals, and final output. Expand it to inspect assignments and dependencies. Users can redirect or stop the operation without leaving the conversation.

## 3. Functional phases and milestones

[Expanded phase details](PHASE-DETAILS.md) specify user workflows, implementation boundaries, dependencies, unresolved choices and acceptance for the 33 original milestones and four mobile-use milestones below. Owner decision, October 25, 2026: finish Phase 5 first, then follow the sequence **Phase 6 → Phase 6A mobile use → Phase 7 → Phase 8**. Phase 6A is a separate stage after Phase 6; Phase 7's numbering and scope are unchanged. Mobile use is not dependent on the deferred VM migration.

### Phase 1 — Prove collaboration and orchestration

[Detailed scope and acceptance](PHASE-DETAILS.md#phase-1--collaboration-and-orchestration-foundations).

**Outcome:** a private local POC demonstrates independent Hermes containers working together under manual and autonomous coordination.

**1.1 — Reproducible environment**

- Bootstrap the application, PostgreSQL, object storage, worker, and supervisor.
- Provision three persistent bot containers with distinct roles.
- Configure providers and verify streaming, approval, stop, plugin loading, and health.

**1.2 — Common coordination tools**

- Implement teammate discovery, messaging, delegation, result delivery, and artifact references.
- Record task relationships and delivery receipts.
- Preserve queued human instructions.
- Enforce delegation, execution, and cancellation limits.

**1.3 — Collaboration across contexts**

- Test explicit delegation to a named bot.
- Test autonomous teammate selection.
- Cover research/writing, file/data processing, and code/review.
- Exercise collaboration from group turns and a one-shot scheduled task.
- Exchange artifacts without sharing private runtime volumes.

**1.4 — Manager orchestration proof**

- Ask one bot to manage two others for a concrete outcome.
- Have it assign dependent work, monitor progress, inspect results, and combine outputs.
- Inject a recoverable failure and verify controlled reassignment.
- Redirect the manager mid-task and cancel the complete orchestration.
- Verify that it cannot control bots or unrelated tasks outside its grant.

**1.5 — Thin web UI and recovery**

- Show roster, chats, basic groups, task status, orchestration cards, approvals, artifacts, and stop controls.
- Test browser disconnect, container recreation, duplicate delivery, pending approval, and interrupted delegation.

**Exit gate:** real runtimes collaborate across task types; manager orchestration works; state survives recreation; retries do not duplicate work; execution continues without the browser.

### Phase 2 — Manage persistent teammates from the web app

[Detailed scope and acceptance](PHASE-DETAILS.md#phase-2--persistent-teammate-management).

**Outcome:** the owner can create, converse with, and direct bots without terminal commands.

**2.1 — Owner onboarding**

- Secure login and session management.
- Provider configuration, model defaults, timezone, and permitted capabilities.
- Actionable setup errors and runtime status.

**2.2 — Bot lifecycle**

- Create, edit, clone, archive, restore, and delete bots.
- Configure role, avatar, model, skills, and tools.
- Provision containers through the existing supervisor.
- Explicitly select which state is copied when cloning.

**2.3 — Complete conversations and groups**

- Permanent chats, attachments, threads, reactions, mentions, and skill references.
- Queue, steer, stop, edit instructions, and retry visibly.
- Create groups, edit membership, inspect handoffs, and handle human escalation.
- Derive progress from runtime events.

**2.4 — Bot-managed lifecycle**

- Add management-grant creation, inspection, expiry, and revocation.
- Let managers propose creating/configuring bots and starting/stopping runtimes.
- Present exact changes and resource implications before approval.
- Execute approved operations through the supervisor.
- Test creating a specialist and incorporating it into an existing orchestration.
- Handle rejection, stale proposals, provisioning failure, and partial completion.

**2.5 — Product shell and staging deployment**

- Three-pane roster/chat/context layout, responsive navigation, and avatar states.
- Private Linux staging deployment with HTTPS.
- Reconnect and continue work from another browser session.

**Exit gate:** the owner can create a bot, assign it management responsibility, approve a new specialist, and receive a collaborative result through the web interface.

### Phase 3 — Files, knowledge, and computer work

[Detailed scope and acceptance](PHASE-DETAILS.md#phase-3--files-knowledge-and-computer-assistance).

**Outcome:** bots produce durable outputs and users can inspect or assist their execution.

**3.1 — Artifacts and library**

- Upload files and ingest links.
- Preview documents, images, tables, code, and outputs.
- Track versions, originating tasks, access grants, search, and downloads.

**3.2 — Memory and skills**

- Inspect, edit, and remove memory.
- Search prior work.
- Create, version, import, enable, and review skills.
- Save successful workflows as skills.
- Allow reviewed improvement proposals, including orchestration procedures.

**3.3 — Computer preview and takeover**

- Separate browser/desktop environment for each bot.
- Live preview, expanded view, takeover, and handback.
- Suspend agent interaction during human ownership.
- Preserve takeover state after disconnect.
- Persist browser sessions across recreation.

**3.4 — Structured review**

- Action previews, editable drafts, approve/deny, expiry, and cancellation.
- Secure input outside transcripts.
- Managers collect required reviews without approving them on the user’s behalf.

Hermes Bot Screen supplies viewing and takeover primitives. [Bot Screen documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/bot-screen)

**Exit gate:** an orchestrated task uses artifacts and skills, handles human-assisted computer work, and returns versioned outputs without overlapping control.

### Phase 4 — Connected-application collaboration

[Detailed scope and acceptance](PHASE-DETAILS.md#phase-4--connected-application-collaboration).

**Outcome:** bots perform cross-application work with enforceable action boundaries.

**4.1 — Connections**

- OAuth lifecycle, encrypted credentials, refresh, revoke, and reconnect.
- Reuse connections across selected bots.
- Keep connector credentials behind scoped backend tools.
- Configurable MCP connections and capability discovery.

**4.2 — Google workflows**

- Gmail search/read, editable replies, and approved sending.
- Calendar availability and approved event changes.
- Drive search/read, imports, and approved file/document creation.

**4.3 — Slack and GitHub workflows**

- Slack search/read, drafts, and approved posting.
- GitHub repository, issue, and PR inspection; proposed and approved changes.
- Handoffs between bots with different app access.

**4.4 — Policy and orchestration**

- Common authorization for connector mutations.
- Editing invalidates stale approvals.
- Standing permissions are scoped and revocable.
- Management authority does not expand connector access.
- Connections preserve all owner-enabled native tools. Connector calls enforce scoped account access and reviewed changes; independently authenticated browser/terminal actions are outside connector review coverage.

**Exit gate:** real Google, Slack, and GitHub tasks complete through orchestration; denied actions do not execute; authentication and uncertain outcomes recover clearly.

### Phase 5 — Ongoing responsibilities and background management

[Detailed scope and acceptance](PHASE-DETAILS.md#phase-5--ongoing-responsibilities-and-background-management).

**Outcome:** bots and managers act on schedules or events while the user is offline.

**5.1 — Routines**

- Natural-language proposals and editable schedule forms.
- Timezones, next-run previews, pause/resume, run now, and history.
- Reuse Hermes scheduled jobs.
- Define overlap, missed-run, and failure behavior.

**5.2 — Event triggers**

Implemented locally; [acceptance and remaining live delivery checks](PHASE5-EVENTS.md), [review](PHASE5-EVENTS-REVIEW.md).

- Authenticated GitHub, Slack, and general webhooks.
- Google change detection through supported subscription/polling mechanisms.
- Deduplicate events and prevent self-triggered loops.

**5.3 — Recurring orchestration**

[Implementation, researched design and acceptance](PHASE5-ORCHESTRATION.md); [code review and corrections](PHASE5-ORCHESTRATION-REVIEW.md). Implemented and deployed locally: selected standing authority/collaborators, immutable occurrence scope, aggregate automated execution accounting, truthful result acceptance and separate pause/stop controls. Two scheduled runs, one signed-local-event run and one actual owner-answer/restart continuation passed with real Hermes contributors; 581 backend tests, responsive browser checks and final fixture cleanup pass. This milestone is accepted for the local scope. The broader connected-service coordination follow-up remains after full Phase 5 completion.

- Schedule a manager to coordinate specified bots.
- Preserve standing management grants, budgets, and initiating-user context.
- Pause for required approvals.
- Resume after authorized decisions and deliver results to the correct conversation.
- Revocation prevents new dispatch and cancels owned pending work according to the user’s selected stop action.

**5.4 — Attention and notifications**

[Detailed researched implementation plan](PHASE5-ATTENTION.md), October 26, 2026. Planning is complete; implementation/acceptance have not started. The proposal separates canonical work state, durable owner read/action state and browser delivery; it covers transactional reconciliation, version-aware unread state, quiet-hour catch-up, generic private push, subscription/logout lifecycle, current deep links, manager summaries and the final Phase 5 exit. Primary documentation and recent original failure reports are linked in the plan.

- Activity inbox, unread results, questions, approvals, and failures.
- Browser push, preferences, quiet hours, and deduplication.
- Manager summaries link to the underlying tasks and decisions.

**Exit gate:** recurring and event-triggered orchestrations continue without the browser and survive restart, duplication, and approval delays.

### Phase 6 — Complete the private web experience

[Detailed scope and acceptance](PHASE-DETAILS.md#phase-6--complete-the-private-web-experience).

**Outcome:** remaining everyday workflows are available through the web app.

**6.1 — Voice**

On hold by the owner, October 27, 2026; proceed with 6.2 first.

- Dictation, editable transcripts, spoken replies, and voice memos.
- Live conversation with interruption and durable records.
- Voice requests can initiate ordinary collaboration and management.

**6.2 — Demonstration teaching** (removed November 10, 2026)

**Removed November 10, 2026.** The owner removed demonstration Teaching (code, UI, tables and recordings) to simplify private computer takeover. This document is kept as history; skills Teaching published remain ordinary installed skills.

[Detailed researched implementation plan](PHASE6-TEACHING.md), October 27, 2026. Selected while voice is held. Implementation is built locally; [review, checks and remaining owner acceptance](PHASE6-TEACHING-REVIEW.md) are recorded.

- Explicit recording of bot-computer interactions.
- Exclude secret entry.
- Generate editable skill drafts; review, test, approve, and version.
- Support taught workflows that delegate to other bots.

**6.3 — Capability discovery**

- Curated catalog of templates, skills, connectors, and trusted plugins.
- Installation, configuration, updates, enable/disable, and removal.
- Show prerequisites and permission changes.
- Include manager-role templates and reusable orchestration skills.

**6.4 — Complete navigation and settings**

- Global authorized search and command palette.
- Shortcuts, themes, responsive mobile-browser workflows.
- Usage, budgets, notifications, export, and settings.
- Consistent onboarding, loading, empty, blocked, failed, and offline states.
- Complete orchestration inspection, management proposals, and cancellation controls.

**Exit gate:** the private-web feature checklist, visual reference reviews, accessibility checks, and voice tests pass.

### Phase 6A — Mobile device use

[Detailed scope and acceptance](PHASE-DETAILS.md#phase-6a--mobile-device-use).

**Outcome:** bots operate assigned mobile devices, with private state, observable results and explicit owner control. Implementation follows Phase 6; Phase 7 retains its existing scope and numbering.

**6A.1 — Device lifecycle and assignment**

- Provision or attach supported Android devices and assign them explicitly to bots.
- Keep app/account/device data private and persistent; expose readiness, start/stop and resource limits.
- Select host-managed or companion-container deployment against actual platform support; leave VM-per-bot migration deferred.

**6A.2 — Agent observation and actions**

- Add supported app launch, accessibility/screenshot observation, touch, text and navigation tools through supported Hermes extensions.
- Validate device identity and current targets; distinguish dispatched input from verified app results.
- Reconcile unknown effects before repetition and attach evidence to the relevant work.

**6A.3 — Live view and owner assistance**

- Provide a live device view, human takeover and explicit handback with enforced ownership.
- Preserve human control through disconnect and coordinate in-flight actions before enabling input.
- Provide secure input and define capture/trace treatment of sensitive information.

**6A.4 — Workflow and recovery acceptance**

- Verify real app workflows, persistent sessions, stale-target handling, restart and disconnect recovery.
- Verify device-specific interactions with existing work, selected routines and permissions without changing their authority.
- Record platform/app limitations and device-specific recovery evidence before proceeding to the existing Phase 7.

**Exit gate:** an actual assigned Android device completes a task with inspectable evidence, safe human assistance and verified persistence/recovery. Agentsims is the research reference; iOS support and specific runtime technology require separate scope validation.

### Phase 7 — Dependable private release

[Detailed scope and acceptance](PHASE-DETAILS.md#phase-7--dependable-private-release).

**Outcome:** the owner can rely on the complete application daily.

**7.1 — Recovery and maintenance**

- Consistent backups and restore onto a clean host.
- Recover conversations, artifacts, skills, routines, grants, and orchestrations.
- Graceful drain, upgrades, compatibility checks, migrations, and recovery controls.

**7.2 — Operational visibility**

- Correlated logs and support identifiers.
- Monitor queue age, stuck tasks, approvals, container health, storage, and usage.
- Display degraded states and enforce resource/spending limits.

**7.3 — Release acceptance**

- Extended unattended execution across schedules, events, and managers.
- Provider outage, server restart, revoked access, and storage recovery tests.
- Export/deletion verification and deployment/recovery runbooks.

**Exit gate:** deployed private application passes all cumulative tests. Sharing begins only after this milestone.

### Phase 8 — Invited teammates and controlled sharing

[Detailed scope and acceptance](PHASE-DETAILS.md#phase-8--invited-teammates-and-controlled-sharing).

**Outcome:** the completed product supports a private workspace.

**8.1 — Membership**

- Invitations and owner/admin/member roles.
- Private bots and DMs by default.
- Explicitly shared groups and artifacts.
- Immediate access revocation.

**8.2 — Team bots**

- Publish a team copy from selected configuration, skills, memories, and files.
- Preserve the original private bot.
- Keep one owning runtime per team bot.
- Separate shared knowledge from member-specific conversations and memory.

**8.3 — Member execution isolation**

- Member-scoped sessions, credentials, workspaces, and browser contexts.
- Agent commands cannot inspect other members’ state or the runtime’s private session store.
- Personal OAuth follows the requester; shared credentials require explicit configuration.

**8.4 — Shared orchestration**

- Managers receive only the bot and operation authority the initiating member can grant.
- Private bots cannot be enrolled without their owner’s authorization.
- Shared groups expose permitted progress and outputs without private history.
- Apply access controls to every existing workflow.
- Add configured team-bot Slack access.
- Test concurrent management, grant revocation, and member removal during execution.

**Exit gate:** members can use shared bots and orchestrations without exposing private conversations, credentials, memory, or artifacts.

## 4. Test and delivery requirements

Maintain a feature-to-milestone checklist. Each milestone requires a working demonstration, appropriate automated checks, updated documentation, and passing earlier tests.

Cumulative tests cover:

| Area | Required scenarios |
|---|---|
| Collaboration | Manual/autonomous handoffs across chats, groups, routines, and connected tasks |
| Orchestration | Dependencies, parallel branches, quality review, reassignment, combined results |
| Management | Create/configure/start/stop; approval, rejection, expiry, revocation, stale configuration |
| Authority | Out-of-scope targets, self-escalation, unrelated-task interference, private-state access |
| Execution | Busy bots, queued instructions, waiting parents, cancellation, loop and budget limits |
| Recovery | Browser disconnect, worker/runtime restart, duplicate or uncertain delivery |
| External actions | Denial, changed payload, expired credentials, provider idempotency |
| Artifacts | Transfer, versions, restricted access, export, restore |
| Routines | Timezones, daylight-saving changes, overlap, missed runs, duplicate events |
| Computer | Takeover, disconnect, handback, persistent login |
| UI | Reference comparisons, all activity states, keyboard access, mobile layouts |
| Team access | Private/shared boundaries, member credentials, removal during active work |

Use deterministic fakes for failure paths and real Hermes/model executions for acceptance. Test real connectors with read-only operations and explicitly approved writes.

Do not automatically replay uncertain external actions. Reconcile existing receipts and provider state first.

## 5. Defaults and boundaries

- Start with three bots; validate the private release with ten registered bots and three active tasks.
- One active execution per bot by default; waiting delegations release slots.
- Groups support up to six bots initially.
- Default delegation depth is three, with ten automatic handoffs per task chain.
- Management grants are task-bound by default; ongoing management requires a standing grant.
- Bot creation/configuration and runtime-wide start/stop require approval or a matching standing permission.
- Bot deletion, credential administration, and access-policy changes remain owner-controlled.
- Connector accounts can be reused across authorized bots; browser sessions remain bot-specific.
- English is the initial UI language, with localization-ready resources.
- Public signup, customer billing, enterprise administration, a native Hermbot mobile app, and desktop packaging are outside this roadmap. Agent-operated mobile devices are included separately in [Phase 6A](PHASE-DETAILS.md#phase-6a--mobile-device-use), after Phase 6 and before the unchanged Phase 7.
- Model quality is evaluated separately from workflow and UI parity.
- The POC is the first working increment of the final architecture, not a disposable mock.
