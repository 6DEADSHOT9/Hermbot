# Hermbot milestone checklist

Phase 1 milestones 1.1–1.5 and its exit gate passed on 2026-10-06. Evidence and practical limits are recorded in [PHASE1.md](PHASE1.md). Phase 2 passed for the owner-approved local scope on 2026-10-07; its acceptance and code review are in [PHASE2.md](PHASE2.md) and [PHASE2-REVIEW.md](PHASE2-REVIEW.md). Linux staging/HTTPS is explicitly deferred by the owner. Phase 3 milestones 3.1–3.4, real exit gate, code review/fixes and cumulative verification passed on 2026-10-07; see [PHASE3.md](PHASE3.md) and [PHASE3-REVIEW.md](PHASE3-REVIEW.md). Phase 4 passed for the owner-approved local scope on 2026-10-19; acceptance and the cleared final review are in [PHASE4.md](PHASE4.md) and [PHASE4-REVIEW.md](PHASE4-REVIEW.md). Phase 5.1 routines passed local acceptance on 2026-10-20; see [PHASE5-ROUTINES.md](PHASE5-ROUTINES.md). Phase 5.2 passed local acceptance on 2026-10-25, including live events and own-write checks for all five providers, final review fixes and 547 backend tests ([evidence](PHASE5-EVENTS.md), [review](PHASE5-EVENTS-REVIEW.md)). Phase 5.3 passed local acceptance with 581 tests, four native manager demonstrations, actual owner-answer continuation after restart and final fixture cleanup ([evidence](PHASE5-ORCHESTRATION.md), [review](PHASE5-ORCHESTRATION-REVIEW.md)). Phase 5.4 and the composed Phase 5 exit passed local acceptance on October 26, 2026, with 638 backend tests, native schedule/event/restart/deduplication evidence, actual owner-confirmed closed-tab exact-result push and guarded fixture cleanup. Phase 6, Phase 6A mobile use and Phases 7–8 remain pending. This checklist mirrors the final approved roadmap in [README.md](README.md); each milestone requires demonstrations, checks, documentation and passing earlier tests.

## Cross-cutting engineering follow-up

- [ ] [Codex/API authentication](CHATGPT-SUBSCRIPTION-AUTH-REVIEW.md#direct-codex-connection): direct Codex browser/device auth and shared broker implemented alongside existing API-key providers. Owner real sign-in, model/tool/image acceptance and supported backend processing remain pending; Phase 6.2 remains gated. Earlier plan-sharing organization denial is recorded as C71, not treated as a proved account-policy diagnosis. Existing assignments and underlying Hermes source remain unchanged.

[Expanded phase details](PHASE-DETAILS.md) cover milestone workflows, boundaries, dependencies and acceptance. Owner update, October 25, 2026: finish Phase 5 first. Android/mobile device use is assigned to [Phase 6A](PHASE-DETAILS.md#phase-6a--mobile-device-use), a separate stage after Phase 6 and before Phase 7. Phase 7's numbering and scope remain unchanged; all Phase 6A milestones are pending.

- [ ] Evaluation framework and broader system evals: **deferred by the owner on 2026-10-19 until after other tasks**. [Research and shortlist](EVALUATIONS.md) are saved; selection and implementation have not started. Resume when the owner returns to this work.
- [ ] [Connected-service work coordination](CONNECTED-WORK-COORDINATION.md): **deferred by the owner again on October 27, 2026, with no new start date**. Its prerequisite Phase 5 is complete; resume the main roadmap with Phase 6.1 voice planning. Research, proposed implementation, acceptance cases and limits are saved. Cover shared admission, automatic correlation, accountable ownership, repeated external actions, handovers and cross-service loops. Implementation has not started; this is not an added Phase 5 gate or a change to phase numbering/evaluation-before-backlog-fixes ordering.
- [ ] Run baseline evaluations **before implementing the current issue-fix backlog**, combine new findings with existing issues, then prioritize fixes and rerun evaluations. This ordering is the owner's 2026-10-19 decision; deferral does not authorize moving the backlog ahead of evaluations. The owner separately authorized C36's acceptance-verifier correction, now verified and closed.

The [issue and learning register](ISSUES.md) records usage reports, historical corrections and current defects in chronological order, with detailed evidence and closure rules. M01–M28 preserve the Manik incident findings, including the corrected M22 diagnosis; the diagnosis did not implement fixes. Update issue status and evidence during each related implementation/review before claiming closure.

## Deferred work after roadmap completion

- [ ] Revisit [VM isolation per persistent bot](VM-ISOLATION.md) with the owner **after Phases 1–8 and their acceptance gates are complete**, per the October 24, 2026 decision. Research and tradeoffs are saved; VM technology, guest privileges and migration are not approved or implemented. Current container architecture and evaluation-before-fixes ordering remain unchanged. This item does not independently defer mobile use.

## Phase 1 — Prove collaboration and orchestration

### 1.1 — Reproducible environment

- [x] Milestone demonstrated and verified.

- [x] Bootstrap the application, PostgreSQL, object storage, worker, and supervisor.
- [x] Provision three persistent bot containers with distinct roles.
- [x] Configure providers and verify streaming, approval, stop, plugin loading, and health.
### 1.2 — Common coordination tools

- [x] Milestone demonstrated and verified.

- [x] Implement teammate discovery, messaging, delegation, result delivery, and artifact references.
- [x] Record task relationships and delivery receipts.
- [x] Preserve queued human instructions.
- [x] Enforce delegation, execution, and cancellation limits.
### 1.3 — Collaboration across contexts

- [x] Milestone demonstrated and verified.

- [x] Test explicit delegation to a named bot.
- [x] Test autonomous teammate selection.
- [x] Cover research/writing, file/data processing, and code/review.
- [x] Exercise collaboration from group turns and a one-shot scheduled task.
- [x] Exchange artifacts without sharing private runtime volumes.
### 1.4 — Manager orchestration proof

- [x] Milestone demonstrated and verified.

- [x] Ask one bot to manage two others for a concrete outcome.
- [x] Have it assign dependent work, monitor progress, inspect results, and combine outputs.
- [x] Inject a recoverable failure and verify controlled reassignment.
- [x] Redirect the manager mid-task and cancel the complete orchestration.
- [x] Verify that it cannot control bots or unrelated tasks outside its grant.
### 1.5 — Thin web UI and recovery

- [x] Milestone demonstrated and verified.

- [x] Show roster, chats, basic groups, task status, orchestration cards, approvals, artifacts, and stop controls.
- [x] Test browser disconnect, container recreation, duplicate delivery, pending approval, and interrupted delegation.

- [x] **Exit gate:** real runtimes collaborate across task types; manager orchestration works; state survives recreation; retries do not duplicate work; execution continues without the browser.

## Phase 2 — Manage persistent teammates from the web app

### 2.1 — Owner onboarding

- [x] Milestone demonstrated and verified.

- [x] Secure login and session management.
- [x] Provider configuration, model defaults, timezone, and permitted capabilities.
- [x] Actionable setup errors and runtime status.
### 2.2 — Bot lifecycle

- [x] Milestone demonstrated and verified.

- [x] Create, edit, clone, archive, restore, and delete bots.
- [x] Configure role, avatar, model, skills, and tools.
- [x] Provision containers through the existing supervisor.
- [x] Explicitly select which state is copied when cloning.
### 2.3 — Complete conversations and groups

- [x] Milestone demonstrated and verified.

- [x] Permanent chats, attachments, threads, reactions, mentions, and skill references.
- [x] Queue, steer, stop, edit instructions, and retry visibly.
- [x] Create groups, edit membership, inspect handoffs, and handle human escalation.
- [x] Derive progress from runtime events.
### 2.4 — Bot-managed lifecycle

- [x] Milestone demonstrated and verified.

- [x] Add management-grant creation, inspection, expiry, and revocation.
- [x] Let managers propose creating/configuring bots and starting/stopping runtimes.
- [x] Present exact changes and resource implications before approval.
- [x] Execute approved operations through the supervisor.
- [x] Test creating a specialist and incorporating it into an existing orchestration.
- [x] Handle rejection, stale proposals, provisioning failure, and partial completion.
### 2.5 — Product shell and staging deployment

- [x] Milestone demonstrated and verified for the approved local scope; deployment deferred as noted below.

- [x] Three-pane roster/chat/context layout, responsive navigation, and avatar states.
- [ ] Private Linux staging deployment with HTTPS — **deferred by the owner on 2026-10-07; local-only operation approved for now**.
- [x] Reconnect and continue work from another browser session.

- [x] **Exit gate:** the owner can create a bot, assign it management responsibility, approve a new specialist, and receive a collaborative result through the web interface.

## Phase 3 — Files, knowledge, and computer work

### 3.1 — Artifacts and library

- [x] Milestone demonstrated and verified.

- [x] Upload files and ingest links.
- [x] Preview documents, images, tables, code, and outputs.
- [x] Track versions, originating tasks, access grants, search, and downloads.
### 3.2 — Memory and skills

- [x] Milestone demonstrated and verified.

- [x] Inspect, edit, and remove memory.
- [x] Search prior work.
- [x] Create, version, import, enable, and review skills.
- [x] Save successful workflows as skills.
- [x] Allow reviewed improvement proposals, including orchestration procedures.
### 3.3 — Computer preview and takeover

- [x] Milestone demonstrated and verified.

- [x] Separate browser/desktop environment for each bot.
- [x] Live preview, expanded view, takeover, and handback.
- [x] Suspend agent interaction during human ownership.
- [x] Preserve takeover state after disconnect.
- [x] Persist browser sessions across recreation.
### 3.4 — Structured review

- [x] Milestone demonstrated and verified.

- [x] Action previews, editable drafts, approve/deny, expiry, and cancellation.
- [x] Secure input outside transcripts.
- [x] Managers collect required reviews without approving them on the user’s behalf.

- [x] **Exit gate:** an orchestrated task uses artifacts and skills, handles human-assisted computer work, and returns versioned outputs without overlapping control.

## Phase 4 — Connected-application collaboration

**Completed October 19 for the local scope.** Real reviewed Google/Slack/GitHub writes, owner-edited Gmail sending/threaded replies and Drive documents, exact Slack denial, provider receipts, native continuations and actual reply/issue readback pass. Historical native reads/imports/handoffs pass the stock verifier after the explicitly authorized C36 fix. All **447 backend tests**, Ruff and the web image build pass; final review is cleared. All 17 goal groups are archived, 18 tasks/27 executions settled and 12 operations terminal, with the original rejected write retained without replay. Three bots/nine private volumes and provider connections are healthy. Hermes source remains unchanged. [Evidence](../test-results/phase4-completion-20261019.json), [review](PHASE4-REVIEW.md).

Connection lifecycle, scoped/revocable standing permissions, stale approvals, isolation and uncertain-outcome recovery combine isolated regressions with the recorded native/public-MCP acceptance. Private actions use actual exact owner decisions; no owner approval was simulated. Local HTTP uses imported Slack tokens; HTTPS OAuth is implemented/tested and remote hosting remains deferred. Native independently authenticated actions remain outside connector review coverage. Other issue fixes remain after baseline evaluations; only C36 was exempted by the owner. Phase 5.1 routines started after this Phase 4 record; broader evaluations and the deferred issue-fix backlog remain pending.

### 4.1 — Connections

- [x] Milestone demonstrated and verified.

- [x] OAuth lifecycle, encrypted credentials, refresh, revoke, and reconnect.
- [x] Reuse connections across selected bots.
- [x] Keep connector credentials behind scoped backend tools.
- [x] Configurable MCP connections and capability discovery.
### 4.2 — Google workflows

- [x] Milestone demonstrated and verified.

- [x] Gmail search/read, editable replies, and approved sending. A separate owner-edited reply passed actual delivered-content, thread, Subject, In-Reply-To/References, native receipt and continuation/readback checks October 19.
- [x] Calendar availability and owner-approved test event creation; actual provider receipt and native continuation verified.
- [x] Drive search/read, imports, and approved document creation; owner-edited Google document receipt and native continuation verified October 19.
### 4.3 — Slack and GitHub workflows

- [x] Milestone demonstrated and verified.

- [x] Slack search/read, drafts, and approved posting; actual post receipt and native continuation verified October 19.
- [x] GitHub repository, issue, and PR inspection; proposed and approved changes. Repository/issue/PR listing passed earlier; fresh owner-approved issue creation and actual item readback pass October 19. No existing individual PR was available in the test repository; PR item routes retain adapter/test coverage, not a claim of live inspection of a nonexistent object.
- [x] Handoffs between bots with different app access.
### 4.4 — Policy and orchestration

- [x] Milestone demonstrated and verified.

- [x] Common authorization for connector mutations.
- [x] Editing invalidates stale approvals.
- [x] Standing permissions are scoped and revocable.
- [x] Management authority does not expand connector access.
- [x] Connections preserve owner-enabled native tools; connector calls enforce account permissions and reviewed changes. Independently authenticated native actions are outside connector review coverage. See the October 11 update in [PHASE4.md](PHASE4.md).

- [x] **Exit gate:** real Google, Slack, and GitHub tasks complete through orchestration; denied actions do not execute; authentication and uncertain outcomes recover clearly.

## Phase 5 — Ongoing responsibilities and background management

### 5.1 — Routines

- [x] Milestone demonstrated and verified.

- [x] Natural-language proposals and editable schedule forms.
- [x] Timezones, next-run previews, pause/resume, run now, and history.
- [x] Reuse Hermes scheduled jobs.
- [x] Define overlap, missed-run, and failure behavior.
### 5.2 — Event triggers

- [x] Complete live Slack/Drive/Calendar/Gmail/GitHub event and own-write acceptance, review corrections and 547 backend tests. Local signed-event/Hermes/restart proof also passes; public ingress is deferred. [Evidence and limits](PHASE5-EVENTS.md), [review/fixes](PHASE5-EVENTS-REVIEW.md).

- [x] Implement authenticated GitHub, Slack, and general webhooks; local polling/Socket Mode transports avoid requiring public ingress.
- [x] Implement Google change detection through durable Changes/History/incremental-sync polling, with explicit baseline expiry and recovery.
- [x] Verify redelivery deduplication and version-specific Hermbot receipt/self-bot suppression in isolated regressions. Real webhook/Hermes deduplication also passes; native actions retain the documented correlation limit.
### 5.3 — Recurring orchestration

[Delivered implementation and evidence](PHASE5-ORCHESTRATION.md), October 26; [review/corrections](PHASE5-ORCHESTRATION-REVIEW.md). Two scheduled native runs, one signed-local-event run and one actual owner-answer continuation after restart pass. All 581 backend tests, responsive UI, Hermes integrity and final fixture cleanup/preservation checks pass. Local milestone acceptance is complete. Connected-service coordination remains deferred until all Phase 5 completes.

- [x] Milestone demonstrated and verified.

- [x] Schedule a manager to coordinate specified bots.
- [x] Preserve standing management grants, budgets, and initiating-user context.
- [x] Enforce the selected run scope through discovery, delegation, automated peer replies, revisions, retries and dispatch; prevent expiry/revocation bypass or ungranted fallback.
- [x] Verify aggregate active-work accounting, slot release at real waits, occurrence identity across edit/admission races and event-triggered manager parity.
- [x] Pause for required approvals; managed current-authority checks reuse the existing decision path (deterministic coverage).
- [x] Resume after authorized decisions and deliver results to the correct conversation.
- [x] Revocation prevents new dispatch and cancels owned pending work according to the user’s selected stop action.
- [x] Complete focused recovery/concurrency/browser/native acceptance, code review/fixes, Hermes integrity checks and fixture cleanup with retained evidence and limits.

### 5.4 — Attention and notifications

[Implemented contract and evidence](PHASE5-ATTENTION.md#19-implementation-and-current-acceptance--october-26-2026), October 26; [review/fixes and composed exit](PHASE5-ATTENTION-REVIEW.md#phase-5-composed-exit--october-26-2026). All 638 backend tests, worker checks, production build, responsive UI, same-session native owner-answer continuation and Hermes/private-volume integrity checks pass. The owner confirmed desktop display and then exact current-result navigation with Hermbot tabs closed. Both retained fixture groups/routines were archived and their scoped grants revoked; the exit trigger/source are archived. All seven original groups, original active routine, bot configuration and live browser settings are preserved.

- [x] Milestone demonstrated and verified for the local scope.

- [x] Activity inbox, unread results, questions, approvals, and failures.
- [x] Browser push, preferences, quiet hours, and deduplication — controlled behavior plus actual owner-confirmed closed-tab exact-result push and guarded fixture cleanup pass.
- [x] Manager summaries link to the underlying tasks and decisions.

- [x] **Exit gate:** background recurring and event-triggered orchestration, restart/deduplication recovery, delayed owner-answer continuation and usable result attention verified for the local scope. Fresh server-initiated scheduled/signed-local-event manager runs completed with two duplicate deliveries and preserved active-attempt restart identity. The actual prior owner-answer delay is revalidated; a subsequent actual closed-tab task/result push opened the exact report, confirmed by the owner. Final guarded cleanup passed. [Exit evidence and explicit limits](../test-results/phase5-exit-final.json).

## Phase 6 — Complete the private web experience

### 6.1 — Voice

Owner hold, October 27, 2026: implementation remains pending; proceed with 6.2 first.

- [ ] Milestone demonstrated and verified.

- [ ] Dictation, editable transcripts, spoken replies, and voice memos.
- [ ] Live conversation with interruption and durable records.
- [ ] Voice requests can initiate ordinary collaboration and management.
### 6.2 — Demonstration teaching (removed November 10, 2026)

**Removed November 10, 2026.** The owner removed demonstration Teaching (code, UI, tables and recordings) to simplify private computer takeover. This document is kept as history; skills Teaching published remain ordinary installed skills.

[Extensive researched implementation plan](PHASE6-TEACHING.md), October 27, 2026. Selected next by the owner while 6.1 is on hold. Covers admitted-input/keyframe capture, protected-input barriers, reviewed evidence, generation, exact task-scoped candidate tests, native publication/versioning, recovery and real acceptance. Implementation is built and locally deployed; [review and verification](PHASE6-TEACHING-REVIEW.md) record 654 backend tests, isolated capture/UI checks and the remaining owner demonstrations. Keep the milestone unchecked until real acceptance passes.

- [ ] Milestone demonstrated and verified.

- [ ] Explicit recording of bot-computer interactions.
- [ ] Exclude secret entry.
- [ ] Generate editable skill drafts; review, test, approve, and version.
- [ ] Support taught workflows that delegate to other bots.
### 6.3 — Capability discovery

- [ ] Milestone demonstrated and verified.

- [ ] Curated catalog of templates, skills, connectors, and trusted plugins.
- [ ] Installation, configuration, updates, enable/disable, and removal.
- [ ] Show prerequisites and permission changes.
- [ ] Include manager-role templates and reusable orchestration skills.
### 6.4 — Complete navigation and settings

- [ ] Milestone demonstrated and verified.

- [ ] Global authorized search and command palette.
- [ ] Shortcuts, themes, responsive mobile-browser workflows.
- [ ] Usage, budgets, notifications, export, and settings.
- [ ] Consistent onboarding, loading, empty, blocked, failed, and offline states.
- [ ] Complete orchestration inspection, management proposals, and cancellation controls.

- [ ] **Exit gate:** the private-web feature checklist, visual reference reviews, accessibility checks, and voice tests pass.

## Phase 6A — Mobile device use

### 6A.1 — Device lifecycle and assignment

- [ ] Milestone demonstrated and verified.
- [ ] Provision/attach supported Android devices with explicit per-bot assignment and private persistent state.
- [ ] Expose readiness, start/stop and resource controls; validate the deployment platform without VM migration.

### 6A.2 — Agent observation and actions

- [ ] Milestone demonstrated and verified.
- [ ] App launch, accessibility/screenshots, touch, text and navigation through supported Hermes extensions.
- [ ] Current device/target validation, evidence attached to work and reconciliation before repeating unknown effects.

### 6A.3 — Live view and owner assistance

- [ ] Milestone demonstrated and verified.
- [ ] Live view, enforced takeover/handback, in-flight action handling and human ownership after disconnect.
- [ ] Secure input and sensitive screenshot/accessibility/recording/trace behavior.

### 6A.4 — Workflow and recovery acceptance

- [ ] Milestone demonstrated and verified.
- [ ] Actual app task, persistent session, stale targets, restart and disconnect recovery.
- [ ] Existing work/routine/permission integration and documented platform/app limitations.

- [ ] **Exit gate:** an assigned Android device completes a real task with inspectable evidence, safe owner assistance and verified persistence/recovery. Phase 7's original scope and numbering remain unchanged.

## Phase 7 — Dependable private release

### 7.1 — Recovery and maintenance

- [ ] Milestone demonstrated and verified.

- [ ] Consistent backups and restore onto a clean host.
- [ ] Recover conversations, artifacts, skills, routines, grants, and orchestrations.
- [ ] Graceful drain, upgrades, compatibility checks, migrations, and recovery controls.
### 7.2 — Operational visibility

- [ ] Milestone demonstrated and verified.

- [ ] Correlated logs and support identifiers.
- [ ] Monitor queue age, stuck tasks, approvals, container health, storage, and usage.
- [ ] Display degraded states and enforce resource/spending limits.
### 7.3 — Release acceptance

- [ ] Milestone demonstrated and verified.

- [ ] Extended unattended execution across schedules, events, and managers.
- [ ] Provider outage, server restart, revoked access, and storage recovery tests.
- [ ] Export/deletion verification and deployment/recovery runbooks.

- [ ] **Exit gate:** deployed private application passes all cumulative tests. Sharing begins only after this milestone.

## Phase 8 — Invited teammates and controlled sharing

### 8.1 — Membership

- [ ] Milestone demonstrated and verified.

- [ ] Invitations and owner/admin/member roles.
- [ ] Private bots and DMs by default.
- [ ] Explicitly shared groups and artifacts.
- [ ] Immediate access revocation.
### 8.2 — Team bots

- [ ] Milestone demonstrated and verified.

- [ ] Publish a team copy from selected configuration, skills, memories, and files.
- [ ] Preserve the original private bot.
- [ ] Keep one owning runtime per team bot.
- [ ] Separate shared knowledge from member-specific conversations and memory.
### 8.3 — Member execution isolation

- [ ] Milestone demonstrated and verified.

- [ ] Member-scoped sessions, credentials, workspaces, and browser contexts.
- [ ] Agent commands cannot inspect other members’ state or the runtime’s private session store.
- [ ] Personal OAuth follows the requester; shared credentials require explicit configuration.
### 8.4 — Shared orchestration

- [ ] Milestone demonstrated and verified.

- [ ] Managers receive only the bot and operation authority the initiating member can grant.
- [ ] Private bots cannot be enrolled without their owner’s authorization.
- [ ] Shared groups expose permitted progress and outputs without private history.
- [ ] Apply access controls to every existing workflow.
- [ ] Add configured team-bot Slack access.
- [ ] Test concurrent management, grant revocation, and member removal during execution.

- [ ] **Exit gate:** members can use shared bots and orchestrations without exposing private conversations, credentials, memory, or artifacts.

## Cumulative verification

The work outcomes and human requests change is implemented and reviewed locally: persistent required results, typed answers, member-ID Slack draft guidance, evidence-aware completion, scoped handoffs and readable reviews. Backend, browser, real-model and preservation evidence are recorded in [WORK-OUTCOMES-REVIEW.md](WORK-OUTCOMES-REVIEW.md). This does not close the remaining Phase 4 private-write acceptance gate.

The complete cumulative test matrix and delivery rules are in sections 4 and 5 of [README.md](README.md). Use [CONTEXT.md](CONTEXT.md) for user decisions, earlier compatible requirements, historical research, and implementation inputs.
