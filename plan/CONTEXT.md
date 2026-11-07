# Hermbot project context

Build Hermbot as a private web application with Grok Bot's user experience, feature workflows, layout, density, and visual style, powered by independent containerized Hermes runtimes. This document carries forward the decisions, research context, implementation boundaries, and handoff from the original Codex conversation.

The approved roadmap is [README.md](README.md), originally copied from the final proposed plan and subsequently amended with the owner's architecture requirements. The source conversation is [archive/conversation.txt](archive/conversation.txt). All original records are preserved in [archive/original-thread.jsonl.gz](archive/original-thread.jsonl.gz); archive completeness and hashes are recorded in [archive/manifest.json](archive/manifest.json). Source record numbers below refer to the original JSONL file.

## Harness architecture decisions

Owner-authorized work refactor, October 13, 2026: ordinary messages use a shared interpretation step before execution. Versioned work requirements, durable owner-change commands, scoped request authorization, descriptive tool references, file publication and shared public work status are implemented above Hermes. Historical work is preserved and upgraded when revised. Hermes source is unchanged; supported middleware, the private runtime adapter and configuration provide integration. Tests cover conversation transitions and external receipts separately from model-reported result quality.

Owner update, October 11, 2026: discard harness independence for now and keep development focused on Hermes. Alternate harness selection and the generalized adapter platform are deferred; the October 10 design below is historical research rather than an active backend refactor requirement. The separately stated rule against changing Hermes source remains in force. The owner subsequently requested keeping all tools available alongside connected applications and authorized implementation. Connection-based tool blocking has been removed, and all four source patches have been replaced through supported middleware and Hermbot's existing private adapter. Connector credentials and authorization remain in the backend; independently authenticated browser/terminal actions are outside connector review coverage. Original bot settings, private volumes and owner groups are preserved.

Owner decision, October 10, 2026: Hermbot builds on top of an unmodified harness. Hermes remains the sole supported harness now. The application must be **harness independent**: a user should be able to select a lighter supported harness by installing/registering an adapter and changing configuration, without changing backend application code. Do not patch harness source, introduce private core hooks or monkey-patch its implementation to implement product features.

Harness-specific integration belongs inside separate adapter packages exposing a common versioned execution/capability contract. The application owns product state, coordination, permissions and shared services; the harness owns its agent loop and native state. Use supported configuration, APIs, plugins and MCP integrations. Discover optional features and expose actual availability instead of assuming identical native tools, memory, skills, stop semantics or session portability. Configuration and owner-authorized knowledge changes are normal harness use, not source modifications.

The initial read-only inspection confirmed four source patches in the prior build and all three running Hermes containers. The October 11 tool-access implementation removes those patches and uses the existing `llm_request` and `tool_execution` middleware plus private skill catalog access. Official-image comparison covers 2,169 upstream Python files; current runtime and preservation evidence is in `test-results/native-tools-audit.json`. Support for another harness and the generalized adapter refactor remain deferred. The rule against changing Hermes source supersedes earlier plans permitting compatibility patches.

## Current state and next step

Phase 1 passed on 2026-10-06. Phases 2 and 3 are complete for the owner-approved local scope on 2026-10-07, including real native/model/web exit gates and the requested code reviews, fixes and cumulative regression verification. The running application is [localhost:8780](http://localhost:8780). See [PHASE3.md](PHASE3.md), [PHASE3-REVIEW.md](PHASE3-REVIEW.md), and [CHECKLIST.md](CHECKLIST.md) for current evidence and scope; Phase 2 evidence remains in [PHASE2.md](PHASE2.md) and [PHASE2-REVIEW.md](PHASE2-REVIEW.md).

The owner explicitly deferred Linux staging and HTTPS and requested running only on this machine for now. Provider access starts with OpenAI-compatible APIs; subscription adapters remain follow-up work. The owner subsequently authorized Phase 3 on 2026-10-07, including an explicit goal to complete it and then review, fix and retest the code. Completion is recorded in [PHASE3.md](PHASE3.md). Phase 4 was subsequently authorized and implemented locally; real three-provider read orchestration and an owner-approved calendar write passed. The remaining reviewed private changes and final post-acceptance review are pending, as recorded in [PHASE4.md](PHASE4.md). Remote deployment remains deferred.

## Historical transfer state and handoff

The following records describe the original transfer before implementation. They are retained as historical context; subsequent implementation authorization and completion above supersede their next-step instructions.

The original thread performed research and planning. It created no application files and never saved its proposed plan because its active Plan mode prohibited writes. Before this transfer, `/Users/dhruv/Workspace/hermbot` was empty. The files added by this transfer are documentation and source archives only. No application milestone has passed.

The user explicitly requested saving the plan in `plan/` before implementation, selected “Yes, start implementing,” and subsequently authorized continuing from a new thread because Plan mode would not turn off. Those decisions remain recorded; they do not need to be asked again. This turn transfers context and the approved plan.

The previous assistant's final handoff was:

> Save this plan to `plan/README.md` first. Then implement Phase 1 only: containerized Hermes bots, collaboration, user-authorized orchestration, and the web POC. Track milestone progress, run the acceptance tests, and document setup and results. Continue through Phase 1 without starting later phases.

The plan-saving prerequisite is now satisfied. The next implementation increment is Phase 1, beginning with milestone 1.1. Do not mark a real-runtime acceptance test complete using a mock or a deterministic fake alone.

## User decisions and exact scope

| Decision | Established scope | Source record |
|---|---|---|
| Product | Same user experience, features, and UI patterns as Grok Bot, with Hermes underneath | 231 |
| Runtime ownership | One bot gets its own container and persistent Hermes runtime | 287 |
| Roadmap structure | Functional phases, POC first, complete application afterward, milestones closing gaps | 323 |
| Audience | Private workspace; no public signup or customer billing | 337 |
| Clients | Web now; desktop only as a separately approved future option; mobile browser included | 337 |
| Collaboration | All functions can collaborate when manually requested or autonomously needed; POC cannot prove just one narrow workflow | 337 |
| Deployment | Local Docker POC, then the same architecture on an always-on Linux server | 353 |
| External actions | Review external writes unless an explicit standing permission covers them; ordinary delegation does not require individual review | 353 |
| Integrations | Gmail, Calendar, Drive, Slack, GitHub, configurable MCP, and browser workflows | 353 |
| Sharing | Private bots first; invited members and explicit sharing only after the private app is complete | 369 |
| Visual fidelity | Close UX and appearance with Hermbot's own branding and avatars | 369 |
| Model access | Owner-managed providers/defaults; bots can use different permitted models | 369 |
| Management | Any bot can manage specified bots on user request; task coordination plus approved lifecycle proposals | 409, 423 |
| First write | Save the roadmap in `plan/` before implementation | 436 |
| Implementation approval | “Yes, start implementing” followed by permission to continue in a new thread | 520, 538 |

Exact user corrections recorded in the question tools:

> Web first then desktop later when this project gets approved. Desktop can only be a future option but we do web for now.

> All the functions should be able to collaborate if needed to both by telling to the bot manually or autonomously like grokbot.

> Private plus shared sounds great but for the poc we do private first, then we can go forward with shared once the app is done.

The selected management answer was “Tasks and lifecycle,” meaning task orchestration plus proposals for bot creation/configuration and runtime start/stop. Full deletion, credential administration, and access-policy changes remain owner-controlled.

An earlier audience question was accepted asynchronously but has no recorded answer; the later answered questions above establish the choices. Do not infer an answer to that earlier question.

## Architecture and ownership

Use React/TypeScript with Vite, Tailwind, accessible Radix components, and TanStack Query. Use Python/FastAPI for the backend, PostgreSQL for product metadata and durable coordination, S3-compatible object storage, a background worker, a dedicated container supervisor, and a Hermbot Hermes plugin. Docker Compose supplies local development and initial single-server deployment. PostgreSQL-backed queues are sufficient initially; no separate message broker was chosen.

Each bot has separate persistent Hermes data, workspace, browser profile, tools, skills, memory, sessions, schedules, and resource limits. Do not mount the same Hermes database in multiple runtimes. Run the full Hermes runtime inside its owning container; configuring only Hermes's terminal Docker sandbox does not meet the user's requirement. Terminal tools initially run inside that runtime container.

The supervisor alone controls container provisioning, health, limits, lifecycle, and upgrades. Its internal interface must be authenticated. Agents do not get the Docker socket or another bot's private volumes. Runtime start/stop state must be distinct from whether the bot currently has an active task.

Hermes owns native execution/session history and agent state. PostgreSQL owns product metadata, logical tasks and delegation relationships, management grants, group transcripts, queues/outbox/receipts, approvals, durable UI events, and rebuildable search projections retaining source identifiers. Avoid competing authoritative execution transcripts or a second independent time-based scheduler.

The adapter uses authenticated Hermes Runs APIs for execution, progress, steering, stopping, and approvals. Probe capabilities at startup; reject incompatible versions. Pin Hermes revisions, service images, application dependencies, and plugin versions. No specific Hermes revision, model, provider credential, object-store implementation, or deployment host was selected in the source thread.

Expose versioned `/api/v1` product APIs for bots, conversations, groups, tasks/delegation trees, orchestrations, grants, proposals, approvals, artifacts, connections, skills, routines, notifications, and settings. SSE carries durable application events; WebSockets carry computer and voice transport. Reconnect requires event cursors plus reconciliation with current state.

## Collaboration and orchestration requirements

The final design uses one Hermbot plugin and backend coordination service for all application-managed collaboration. Its tools discover authorized teammates/capabilities, send informational messages, delegate tasks, inspect status, return results, publish/reference artifacts, request user input/approval, plan orchestration, and propose permitted lifecycle changes.

Handoffs need stable identities, authenticated sender and recipient, originating conversation, parent task, artifact references, deadline/status, and receipts. A model-supplied sender field must not permit impersonation. Distinguish informational messages, task requests, and task results. A delivery acknowledgement means acceptance, not completion.

Delegation is asynchronous. Logical tasks and Hermes runs are different records: an execution run can finish while its task waits for a teammate or a human. Waiting parents release execution slots; the worker delivers child results and starts the correct continuation when the parent is ready. This avoids deadlocks when bots request work from each other. Preserve already queued human instructions and serialize conflicting work.

The browser must not drive group execution. Maintain one authoritative room transcript, participant sessions in the owning containers, mentions/handoffs, bounded turns/time/spend, pause/stop, approvals, escalation, and one active server-side driver per room. Closing the client must not stop the group or lose its result.

Any ordinary bot can become a manager through a scoped grant specifying manager identity, target bots, allowed operations, budget, and duration. Task-bound grants are the default; ongoing management needs an explicit standing grant. Managers can plan dependencies, choose permitted bots, assign work, monitor progress/blockers/budgets, steer their tasks, reconcile and reassign recoverable failures, review outputs, and combine the final result.

Managers may propose creating/configuring bots or starting/stopping whole runtimes. Show exact changes and resource implications for approval or match a standing permission. Apply configuration changes between runs; drain or explicitly interrupt active work first. Managers cannot self-escalate, read private histories/memory/credentials, approve actions for the user, interfere with unrelated work, or acquire extra connector access through delegation. Stopping an entire runtime needs separate authority from cancelling an owned task tree. Record the initiating user for each operation and support revocation/stop in the UI.

Never replay uncertain external actions automatically. Reconcile receipts, active attempts, and provider state first. Use durable identities and provider idempotency where available; do not claim universal exactly-once effects.

## Defaults and exclusions

| Setting | Initial target |
|---|---|
| POC roster | Three persistent bots with distinct roles |
| Private release capacity validation | Ten registered bots and three active tasks |
| Per-bot execution | One active execution by default; waiting delegations release slots |
| Group size | Up to six bots initially |
| Delegation depth | Three |
| Automatic handoffs | At most ten per task chain |
| Management authority | Task-bound by default; standing grant for recurring management |
| Connections | Reusable across explicitly authorized bots |
| Browser sessions | Separate per bot |
| Initial language | English with localization-ready resources |

Earlier plan wording also calls for owner settings exposing collaboration limits. Preserve this requirement when implementing settings. Public SaaS, public signup, customer billing, enterprise administration, native mobile apps, and desktop packaging are outside this roadmap. Model/task quality must be assessed separately from matching the UI and workflows. The POC is the first working increment of the final architecture.

## UX and visual context

The product's central journey is creating a persistent teammate, giving it responsibility and app access, following/steering its work, reviewing an output or action, assisting through computer takeover when needed, and converting successful work into a skill or routine. Bots can collaborate in groups or as a user-authorized manager, and background work returns to the correct conversation after the app closes.

Use a three-pane desktop layout: roster/search/groups/connections/library on the left; conversation/progress/drafts/approvals/artifacts in the center; computer preview/routines/context on the right. Open the right panel when useful, preserve spacious chat otherwise, and support expanded computer takeover. Supporting screens include creation, bot settings, connections, routines, library, activity, and account settings. Model configuration can live under advanced bot settings.

Use quiet surfaces, restrained borders, rounded controls, recognizable colored/animated avatars, and concise status text. Preserve idle, thinking, working, waiting, blocked, completed, and failed distinctions. Animation alone cannot communicate whether user intervention is needed. Build a reference sheet for typography, spacing, colors, borders, avatars, motion, and interaction states. Validate loaded screens and recovery/empty/offline states against references, including keyboard access and mobile browser layouts.

Draft cards expose editable content and recipients. Approval cards expose the actual proposed operation and inputs. Artifact cards provide previews/downloads and links to originating work. The orchestration card shows manager, participants, progress, dependencies, blockers, proposals, and final output, with redirect/stop controls in the conversation. The activity inbox distinguishes unread outputs, questions, approvals, failures, and ongoing work. Infrastructure details belong behind settings.

Computer takeover needs exclusive ownership: pause agent interaction while the human controls it, preserve takeover state after disconnect, and provide explicit handback/recovery. Secure entry stays outside transcripts. Connector calls enforce exact mutation approval; editing an action invalidates its existing approval. The October 11 owner amendment leaves independently authenticated browser/terminal actions outside connector approval coverage and keeps native tools available.

The visual reference retained from the earlier research is [archive/grokbot-design-reference.png](archive/grokbot-design-reference.png). It shows the public design article and embedded interface examples; it is not a signed-in screen capture of a working product. Use source links and future reference review to complete the screen set.

## Research context and engineering findings

The original research covered official Grok Bot documentation, releases, design examples, workflow guides, and independent reports, then compared Nous Research Hermes Agent. Historical findings below are carried forward from that research; this transfer did not freshly verify product claims.

Grok Bot's documented feature set included persistent named bots/roles/avatars/memory; managed computer/browser access; app connections/plugins; messages, threads, reactions, mentions, attachments, and steering; artifact previews and library; reusable skills; scheduled/event routines; demonstration teaching; groups/asynchronous handoffs; editable drafts and approvals/standing rules; dictation/live voice/voice memos; mobile access; templates; and Team Bots.

The initial research described one shared cloud computer per account with separate bot screens, rather than security isolation between bots. Hermbot deliberately uses the user's stronger per-bot runtime/volume/browser isolation. “Connect once, enable for selected bots” must come from centrally managed connector access; browser logins are not automatically shared.

The original Grok Bot report referenced v0.66.0 released October 2, 2026, reported attachment limits of six files per desktop message, 25 MB for documents/images/audio and 200 MB for video, and demonstration recordings up to ten minutes. These were observations about the reference product, not accepted Hermbot limits. Historical price examples, platform-specific mobile sharing limitations, and availability caveats also remain in the full conversation; they do not add customer billing or native clients to Hermbot's scope.

The reference product's mobile limitations included desktop-only routine editing/testing and some unavailable advanced/teaching controls; Hermbot's responsive web scope still includes useful mobile approvals and computer access. Hiding a reference-product bot did not stop routines; a template copy did not share credentials/history; Team Bots combined shared setup/knowledge with private member contexts. Reflect these distinctions when designing Hermbot archive, clone, template, and team features rather than equating visibility with execution.

Reported reference-product failures included silent endings, missing/disappearing results/messages, failed sends, login resumption, hung work, duplicate notifications, unpredictable usage, and coordination overhead. Independent reports were anecdotes, not measured reliability or success rates. These findings motivated clear terminal states, concrete results, usage/budget visibility, access recovery, and deliberate orchestration limits.

Hermes research found Bot Mode with roles, models, memory, skills, permanent chats, groups, routines, and per-bot tools/MCP; Bot Screen with headless Linux preview/takeover; extensible plugins/hooks/MCP; HTTP/SSE Runs APIs; JSON-RPC integration; scheduled jobs; searchable sessions; and model/provider choice. Stored memory and skill improvement do not automatically retrain the model. Profiles alone are not filesystem security boundaries. Hermes Cloud was an available hosting option, but the user chose local Docker followed by an always-on Linux server.

The built-in `message_agent` tool was documented as available only in canonical Bot Chats on Bot-Mode-managed installs, excluding ordinary/group/CLI contexts. Headless deployment also requires Bot Chat/metadata markers. This is why the final plan uses a custom plugin rather than assuming native messaging covers every required context. Plugins require explicit enablement and correctly configured toolsets/prerequisites.

An earlier architecture proposed native `hermes peer` as transport for direct gateway-to-gateway DMs/runs. Research described peer API keys, asynchronous run IDs/status/stop/idempotency, and desktop relay behavior distinct from native peer routing. The final architecture supersedes that proposal with the unified plugin/backend coordination path. Native peers remain research context, not an implementation dependency chosen by the final roadmap.

Native same-gateway rooms and rooms spanning gateways have different driver/relay/authority behavior. If any native room mechanism is reused, probe its actual capabilities, preserve one authority, and fence the previous writer before takeover; a disconnect or stop request does not establish that the old writer can no longer commit. Peer registration alone does not establish complete room-history replication.

Additional runtime details recorded in source outputs need verification against the pinned build before coding:

- Runs idempotency keys were scoped by profile/credential, reserved durably, rejected conflicting payloads, and retained for 24 hours after the last status change. Application durability cannot depend only on that retention window.
- SSE event buffers were transport state with a five-minute expiry; active work remained tracked by polling/control APIs. Durable application events and state reconciliation are still needed.
- Stop returned `stopping` before executor exit and then `cancelled`. Do not report a worker as stopped while it is still executing.
- Detached Hermes delegation required server-side session history; caller-supplied history or Responses chaining could force synchronous behavior. Native completion persistence did not itself start an unsolicited model turn. Hermbot's worker owns task continuation.
- Scheduled jobs were governed by cron's limits rather than the general Runs concurrency cap. Enforce the chosen per-bot admission limit across interactive, group, routine, and delegated work.
- API/session/job/capability surfaces were authenticated. Hidden/compacted sessions and compression lineage need careful correlation when rebuilding history projections.
- Source Docker documentation described immutable image-owned code, persistent `/opt/data`, supervisor/restart behavior, derived images for durable additions, and browser dependencies. Pin and probe rather than copying mutable `latest` examples.
- Bot Screen needs compatible Linux desktop dependencies and explicit agent/human ownership; containerization does not make that workflow automatic.

## Research links preserved from the source

| Area | Original reference |
|---|---|
| Grok Bot product | https://x.ai/bot |
| Grok Bot releases | https://x.ai/changelog/bot |
| Grok Bot design | https://x.ai/news/designing-grok-bot |
| Grok Bot onboarding | https://docs.x.ai/grok-bot/get-started |
| Bots and templates | https://docs.x.ai/grok-bot/bots |
| Chat and collaboration | https://docs.x.ai/grok-bot/chat-and-collaboration |
| Computer and apps | https://docs.x.ai/grok-bot/computer-and-apps |
| Files and results | https://docs.x.ai/grok-bot/files-and-results |
| Skills, teaching, routines | https://docs.x.ai/grok-bot/skills-routines-and-automations |
| Approvals and privacy | https://docs.x.ai/grok-bot/approvals-security-and-privacy |
| Mobile | https://docs.x.ai/grok-bot/mobile |
| Settings and notifications | https://docs.x.ai/grok-bot/settings-and-notifications |
| Team Bots | https://docs.x.ai/grok-bot/team-bots |
| Troubleshooting | https://docs.x.ai/grok-bot/troubleshooting |
| Work guide | https://x.ai/bot/guides/grok-bot-for-work |
| PM guide | https://x.ai/bot/guides/grok-bot-for-pms |
| Hermes overview | https://hermes-agent.nousresearch.com/docs/user-guide/features/overview/ |
| Hermes Bot Mode and peers | https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode |
| Hermes Docker | https://hermes-agent.nousresearch.com/docs/user-guide/docker |
| Hermes API server | https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server |
| Programmatic integration | https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration |
| Hermes plugins | https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins |
| Plugin authoring | https://hermes-agent.nousresearch.com/docs/developer-guide/plugins |
| Event hooks | https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks |
| Bot Screen | https://hermes-agent.nousresearch.com/docs/user-guide/features/bot-screen |
| Profiles and isolation | https://hermes-agent.nousresearch.com/docs/user-guide/profiles |
| Memory | https://hermes-agent.nousresearch.com/docs/user-guide/features/memory |
| Skills | https://hermes-agent.nousresearch.com/docs/user-guide/features/skills |
| Cron | https://hermes-agent.nousresearch.com/docs/user-guide/features/cron |
| Toolsets | https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference |
| Hermes Desktop | https://hermes-agent.nousresearch.com/docs/user-guide/desktop |

The full conversation and decoded tool results preserve additional links, independent reports, failed retrievals, and exact snippets. Some saved tool results were already truncated by their original tool calls; the lossless archive preserves what was actually saved, and does not invent missing research content.

## Validation and delivery

Every milestone needs a working demonstration, appropriate automated checks, updated setup/interface documentation, and passing prior acceptance tests. Maintain a feature-to-milestone checklist, account for failures, and close each phase only after its exit gate passes. [CHECKLIST.md](CHECKLIST.md) mirrors all final roadmap milestones and requirements; no boxes are complete at transfer time.

Phase 1 must demonstrate real manual and autonomous collaboration in research/writing, file/data processing, and code/review; the same tools from group and one-shot scheduled contexts; isolated artifact transfer; a manager directing two bots with dependencies and combined results; recoverable failure/reassignment; live steering; tree cancellation; grant boundaries; browser disconnect; runtime recreation; duplicate delivery; pending approval; and interrupted delegation.

Use deterministic fakes to exercise failure paths and real Hermes/model runs for acceptance. Later connector acceptance includes read-only operations and explicitly approved writes. Cumulative tests also cover queues/busy bots, parallel branches, stale/rejected/expired/revoked approvals/grants, budget/loop limits, connection expiry, artifact permissions/versioning/export/restore, timezone/DST/overlap/missed schedules, duplicate events, takeover disconnect/handback, UI references/accessibility/mobile, and eventual member removal/private-state isolation.

## Environment evidence and open implementation inputs

The original environment checks reported Docker client/server `29.8.1`, no host Node executable, and Python `3.9.6`. These are historical checks, not a fresh environment guarantee. Containerized Node/Python development was selected to avoid dependence on host installations.

At transfer time, implementation still needed a pinned Hermes build/image, a provider/model and configured credentials for real runs, the internal authorization/proposal schemas, final queue and event contracts, a concrete S3-compatible service, and later staging-host details. The local inputs have since been implemented and verified in Phases 1–2; staging details are deferred by the owner. These inputs do not reopen the settled product decisions. Research alone did not prove that every described capability worked together in an installed Hermes build; the acceptance reports record the actual runtime evidence.

The initial five-step product outline, native-peer architecture, and pre-management eight-phase roadmap are historical context. The final eight-phase roadmap with management is authoritative. Earlier compatible details are retained here; where the design changed, use the final plan.

## Phase 1 provider clarification — 2026-10-06

The owner requested configurable OpenAI-compatible APIs first. Hermes-supported Codex subscription and Claude subscription integrations should follow; they are not implemented or claimed by the Phase 1 custom API adapter. Provider URL, model identifier, and key stay owner configured.

## Phase 2 implementation authorization (2026-10-07)

The owner requested completing Phase 2, creating a goal, then reviewing the completed code and fixing any discovered issues. The owner subsequently deferred the roadmap's Linux staging/HTTPS deployment: “for now we will run the system on this machine only. this will be discussed later.” Complete and verify the local Phase 2 milestones, with deployment explicitly recorded as deferred. Do not treat the missing staging host as a blocker or start later phases.


## Phase 3 authorization and completion (2026-10-07)

The owner requested: “Let's start building phase 3. Make a goal to complete phase 3 and build it. and also do a code review and fix any issues which arrive after phase 3 completion.” An explicit goal covered milestones 3.1–3.4, the real local exit, cumulative Phase 1–2 acceptance and post-exit review/fixes/retests. All passed. The implementation adds immutable library uploads/link ingestion/previews/versioning/grants/search/downloads; native memory and skill management, workflow drafts and owner-reviewed learning; isolated computer preview/live takeover/handback with persistent ownership, epoch/input fencing and browser profiles; editable exact reviews and metadata-only secure-input completion.

The real exit root is `67a2ed21-14c0-4d93-b877-90b68c1c03ca`: Sage coordinated Scout and Quill using granted input and enabled skills, collected owner-assisted browser input, and returned two immutable report versions. A separate actual in-flight tool proof recorded one effect, an 8.136-second takeover wait, durable pause and handback continuation. Final verification: 85 backend checks, Ruff, TypeScript/production build, fresh native lifecycle/skills, owner browser controls/history/reconnect/disconnect, real collaboration and complete recovery checks. The read-only completion audit verified five healthy compatible Phase 3 runtimes, 15 isolated volumes, 85 artifact downloads and 17 knowledge versions, no pending work/operations/reviews/knowledge proposals/human leases, and absence of the dummy secure-input canary from product records.

Concrete review findings and corrections, evidence and practical limits are in [PHASE3-REVIEW.md](PHASE3-REVIEW.md) and [PHASE3.md](PHASE3.md). Runtime image: `hermbot-hermes:phase3`; historical app/test image names remain compatible. Native tools are excluded during human ownership; arbitrary earlier background processes are outside this trusted single-owner admission boundary. Office previews extract bounded text. Linux staging/HTTPS remains explicitly deferred. Subscription model providers and Phase 4 connected applications were not started. Original planning archives were preserved.

## Skills/tools improvement authorization and delivery (2026-10-10)

After the automatic work-item routing correction, the owner accepted automatic context selection and requested “plan extensively and implement” the proposed skills/tools/handoff improvements. The plan is [SKILLS-TOOLS.md](SKILLS-TOOLS.md), with findings and fixes in [SKILLS-TOOLS-REVIEW.md](SKILLS-TOOLS-REVIEW.md). Ordinary messages require no intent, work-context or skill picker. The responding model selects relevant enabled procedures, prepares against actual native/application access and explains exact blockers. Handoffs explicitly share admitted artifacts/attachments and expected output; recipients prepare independently. Permission changes and native continuations invalidate preparation, and blocked/unprepared work cannot report success. Skill consultation does not evaluate templates or inline commands.

Local delivery includes 265 passing cumulative backend tests, real native automatic selection/blocked access/attachment handoff evidence and desktop/mobile inspection. Temporary procedures are removed and the fixture archived; original core skills, private volumes, provider connections and both owner-created groups remain. This improvement does not close Phase 4's remaining reviewed private-write acceptance gate, start later phases or reverse the owner's deferral of staging/HTTPS and multi-user deployment.

## Shared work-controller authorization and delivery (2026-10-13)

The owner requested implementing the general solution for conversation interpretation, changing requirements, waiting managers, artifact handoffs and action authority. The implementation is application-level: shared owner-message intake precedes execution, requirement revisions and durable commands record changes, and connector effects use verified account/resource references and owner-request scopes or explicit review. It preserves Hermes source and private bot state. File transfer uses Hermbot's authenticated adapter rather than copying private histories or passing binary files through model arguments. Native execution uses a supported large turn ceiling and the existing visible active-work budget; no artificial checkpoint/yield loop is added.

Automatic intake is enabled for ordinary `/api/v1/messages` requests. Explicit legacy intents and `/api/v1/tasks` retain compatibility. Historical records are not replayed. Code review fixed correction/settlement races, combined updates in one message, partial requirement preservation, selected coordinator/grant preservation and useful intermediate-artifact publication. Regression scenarios include changing an output format while a coordinator waits; the implementation has no format-specific execution branch.

Real acceptance records 24 configured-model interpretations, four controlled quality assessments and native manager/helper work with an owner revision through another bot and an actual shared artifact read. A separate native run completed 30 consecutive model/tool turns. Desktop/mobile checks passed; acceptance groups are archived. Connector effect regressions use mocked providers, without live external changes. Controlled quality assessments are evaluation evidence, not a universal production truth check. See the executable checks documented in the root README and their JSON evidence in `test-results`.

## Concurrent tool admission and takeover (2026-10-13)

After investigation, internet research and an implementation explanation, the owner requested implementing the tool-lock correction. Hermbot's exclusive lock around Hermes continuations could make a later worker wait for an earlier one blocked on that same lock. The lock originated from the Phase 3 takeover-drain requirement; single-tool takeover acceptance did not cover reversed parallel middleware entry. The fix uses shared nonblocking admission for tool callbacks and exclusive draining only in the owner adapter after the native human lease closes admission. Exact viewer and epoch checks gate readiness; timeouts keep input disabled. Ordinary runs remain continuous. Hermes source is unchanged.

Validation includes 413 cumulative backend tests, ten isolated reversed-entry trials using the pinned Hermes ordering gate, real native lease draining across two callbacks, process-exit cleanup, and desktop/mobile status checks. Real model execution completed six file reads and two searches in one batch with 37–75 ms tool durations. A foreground takeover waited 11.476 seconds, handback resumed the task and its effect counter remained one. The acceptance runner's earlier network/timing failures are retained separately from verified cases; no external account change was made. Runtimes were updated through the owner lifecycle while idle, preserving configuration and private volumes. See the root README's executable checks and `test-results/tool-gate-*.json`.


## Communication, dependencies and accepted deliverables (2026-10-13)

After researching analogous failures and explaining the implementation, the owner requested implementing the general orchestration correction. New ordinary work uses durable scoped teammate inboxes, immediate delegation, named waits, one accountable assignment per requirement, defined required/optional contributions, and revision-bound acceptance records. Informational messages create no child assignment. Published files are evidence; contributions cannot fulfill the shared deliverable. The accountable assignment reads contributed artifacts and records its review when submitting the final result. Successful provider receipts survive incomplete review. Historical executions and receipts retain their original semantics; supported Hermbot request middleware handles Chat Completions and Responses without editing Hermes source.

Review covered owner corrections, delivery recovery, contribution replacement, trust boundaries and preservation of successful external receipts. A failed real acceptance caught a request-format integration error and is retained separately; fixture recovery never repeated an external effect. Local deployment preserves bot configuration, connections and private volumes. This work does not close Phase 4's remaining real private-write gate or start later roadmap phases. Validation evidence is in `test-results/coordination-*.json` and the root README's executable checks.

## VM isolation proposal deferred (2026-10-24)

After discussing mobile-device placement and researching a VM per persistent bot to provide greater OS freedom, the owner instructed: “keep this plan for later after we complete it all first.” Preserve the [proposal, critique, sources and pilot requirements](VM-ISOLATION.md) for reconsideration after completing the existing Phases 1–8 and acceptance gates. Current bot containers remain the implementation; no VM migration or platform selection is authorized. This does not reorder evaluations and backlog fixes or independently defer the separate mobile-use request. Revisit with the owner before implementation; roadmap completion alone does not start the migration.

## Expanded phases and unassigned mobile use (2026-10-24)

The owner requested: “expand all the phases and well discuss where to put mobile use.” [PHASE-DETAILS.md](PHASE-DETAILS.md) expands all eight phases and 33 existing milestones into user workflows, boundaries, dependencies, gaps and acceptance criteria. Existing completion evidence, evaluation-before-backlog-fixes ordering, deferred hosting and post-roadmap VM isolation remain in place. The Android/mobile-use scope, Agentsims research references and placement alternatives are recorded separately in that document without assigning a phase or starting implementation. Phase 6.5 was previously suggested in conversation but was never added or approved; discuss placement with the owner after reviewing the expanded roadmap.

## Phase 5 priority and mobile placement (2026-10-25)

The owner instructed: “let us first finish phase 5. Add mobile use after phase 6 and without disturbing phase 7. so what's left of phase 5 ?” Mobile use is now a separate **Phase 6A**, placed between Phase 6 and the unchanged Phase 7. Its device lifecycle, agent actions, owner controls and recovery milestones are pending; this placement does not start mobile implementation or approve VM migration. Phase 5 remains the next focus: 5.1 routines have recorded local acceptance; 5.2 event triggers, 5.3 recurring orchestration, 5.4 attention/notifications and the phase exit remain pending. The immediate reply reports remaining scope; it does not launch implementation. The prior evaluation-before-backlog-fixes ordering remains in force.


## Phase 5.2 implementation (2026-10-25 IST)

The owner authorized implementation of the researched event-trigger plan. [Implementation and acceptance](PHASE5-EVENTS.md) and [review/corrections](PHASE5-EVENTS-REVIEW.md) record the delivered source/trigger UI, durable inbox, isolated collector, provider adapters, ordinary work admission and lifecycle guards. The local signed-event-to-Hermes/restart proof and live Drive/Calendar/Gmail/GitHub baselines pass; full provider-delivery acceptance remains open. Slack needs an app-level token/subscriptions saved privately by the owner. Public ingress was not authorized or deployed. Additional-service extensibility remains deferred until after Phase 5, as the owner requested; 5.3/5.4 remain pending.


## October 25, 2026 — Phase 5.2 live acceptance completed locally

The owner configured Slack Socket Mode privately, approved exact Google/Slack/GitHub canaries and made real outside-Hermbot changes in all five providers. Each produced one completed native report; own-write checks passed. Live checks exposed stale legacy finish handling, invisible Drive versions/Google Doc conversion and daily-cap ordering; corrected and recorded as C47–C49 in [ISSUES.md](ISSUES.md). A fresh approved Doc was verified through the actual collector read-only export check and its retained unstarted event was ignored by ordinary admission. All 547 backend tests pass; Hermes source remains unchanged. Acceptance group/triggers are archived, configured sources and private Slack setup are retained for reuse, and external test objects remain. [Precise evidence and limits](PHASE5-EVENTS.md). Phase 5.3 recurring orchestration is next; 5.4 notifications and the phase exit remain pending. Additional-service extensibility, mobile placement and hosting deferrals remain as previously agreed.

## October 26, 2026 — Connected-service coordination deferred until Phase 5 completion

The owner requested deep research into multiple eligible teammates responding to the same connected-service request, then instructed that implementation wait until Phase 5 is completely finished. [Research and proposed solution](CONNECTED-WORK-COORDINATION.md) preserve findings from durable webhook sessions, message correlation, consumer groups, idempotency, integration-loop prevention and recent agent preprints, with primary-source links and limitations.

The proposal extends existing Hermbot admission, work ownership and connector operation records: correlate before creating duplicate work roots; assign one accountable teammate per logical request and purpose; retain legitimate independent automations and follow-ups; separately protect external effects; reconcile uncertain outcomes; check current authority during handover; track causation across services. It applies beyond Slack and keeps Hermes source and owner-enabled native tools unchanged. Phase 5.3, 5.4 and the phase exit remain first. This documentation starts no implementation or broad evaluations, adds no Phase 5 gate and preserves the prior evaluation-before-backlog-fixes ordering, mobile placement and VM deferral.

## October 26, 2026 — Phase 5.3 research and detailed plan

The owner requested extensive planning and deep internet research into recurring orchestration and analogous software-system problems. [PHASE5-ORCHESTRATION.md](PHASE5-ORCHESTRATION.md) records the current-code map, primary-source comparisons, recent original scheduling/queue reports, proposed owner workflow, optional management contract, scope/expiry rules, occurrence identity, contribution acceptance, aggregate automated-execution budgets, real waits, controls, recovery, incremental windows, implementation slices and acceptance/cleanup requirements.

Key inspected gaps: saved routines do not pass a standing grant into ordinary task creation; automated peer replies use separate roots without the source grant; existing assignment/update budget aggregation does not automatically cover those informational replies. Planned effective scope follows the occurrence through descendants, peer responses, owner revisions and replacement roots, with live authority checks and no ungranted fallback. Reuse the existing no-agent Hermes cron wake, PostgreSQL-backed work/controller, typed input/reviews and connector receipts. No Hermes source modification, forced checkpoint/yield protocol, additional workflow platform, new provider or cross-trigger coordination is planned for 5.3. Implementation and acceptance have not started; 5.4 and the Phase 5 exit remain pending, followed by the separately saved connected-service coordination work.


## October 26, 2026 — Phase 5.3 implemented; owner continuation acceptance pending

The owner authorized implementation of the researched 5.3 plan. The local application now supports optional standing management permission/collaborator selection in schedule and event forms, server-frozen occurrence scope, current-authority intersection, inherited run identity, shared automated execution budget, whole-run stop and precise result/failure acceptance. Existing Hermes cron wake-up, native execution, typed questions/reviews and connector receipts are reused; no upstream Hermes source or owner-enabled native tools were changed. [Implementation/evidence](PHASE5-ORCHESTRATION.md) and [code review/corrections](PHASE5-ORCHESTRATION-REVIEW.md) record the details. C50–C54 record integration/review defects and fixes in the canonical issue file.

All 581 backend tests pass. Two scheduled manager occurrences and one signed-local-event occurrence each produced two real terminal-backed contributor artifacts, manager inspection/acceptance and one final report in the correct distinct thread. Responsive browser inspection, final TypeScript build and pinned-source/private-volume integrity checks pass. Completed schedule/event/source fixtures are archived. Original owner bots, groups and active routine definitions were compared with the before snapshot and preserved.

The actual owner-answer test remains open in “Phase 5.3 recurring acceptance db8e16f80031”, task `44b60bfa-37b4-4639-9884-bcf2e78ab8d2`. The same unanswered question and settled native attempt survived API/worker restart and final deployment without another attempt. No answer was simulated. Resume after the actual owner choice, verify one same-session continuation and accepted delivery, then archive the remaining paused wait routine/group and revoke only its fixture grant. The 5.3 milestone remains unchecked until that step and cleanup pass. Phase 5.4 and the overall Phase 5 exit remain next; connected-service coordination still follows full Phase 5. Mobile placement, VM/hosting deferral and evaluation-before-backlog-fixes decisions remain unchanged.


## October 26, 2026 — Phase 5.3 local acceptance complete

The owner answered the exact waiting question with “Continue local test” in Hermbot, then asked to check. Verification found one authenticated answer event and continuation of task `44b60bfa-37b4-4639-9884-bcf2e78ab8d2` in its original native session. The original pre-restart attempt remains; two normal later turns delegated/waited and then inspected/accepted both contributors. The final report and all four manager demonstrations now pass receipt verification. Eight actual helper calculations/artifacts, manager inspection, accepted contributions and distinct run delivery are retained. No connector operation was created by these fixtures.

Guarded final cleanup archived the remaining wait routine/group, revoked only the fixture grant and confirmed no unfinished fixture execution. Original owner bots, groups and active routine definitions match the before snapshot. The driver initially used a nonexistent POST conversation-archive route; corrected it to the existing DELETE group endpoint and verified cleanup, with no product API change. The roadmap/checklist now mark 5.3 complete for the local scope; the full backend result remains 581 passed and earlier UI/source-integrity evidence remains valid. [Implementation/evidence](PHASE5-ORCHESTRATION.md), [review](PHASE5-ORCHESTRATION-REVIEW.md), [native proof](../test-results/phase53-live.json) and [restart/answer proof](../test-results/phase53-restart.json).

Phase 5.4 attention/notifications is next, followed by the overall Phase 5 exit. Connected-service coordination remains after full Phase 5, and mobile use remains Phase 6A after Phase 6. Broad evaluations, VM migration and hosting retain their previously recorded deferrals; no unrelated backlog fixes were started.


## October 26, 2026 — Phase 5.4 researched implementation plan

The owner requested extensive 5.4 planning, deep internet research into similar implementations and lessons from broader software-system problems. [PHASE5-ATTENTION.md](PHASE5-ATTENTION.md) records read-only inspection of current event/work/question/review/session/UI paths; primary references to Knock, Linear, GitHub, Slack, Alertmanager, transactional outboxes, PostgreSQL, Web Push/VAPID/encryption/browser contracts and Python push transport; and original Novu issue/PR evidence from April–September 2026. Reported upstream defects are distinguished from confirmed Hermbot behavior and published-version guarantees.

The proposed implementation is a durable current-state attention view plus a PostgreSQL pending/delivery queue and standards browser push, built above unmodified Hermes. It separates domain resolution, read versions and transport outcome; covers input/review expiry/replacement, managed occurrence identity, accepted results, multi-tab badges, delayed commits, quiet/DST/burst catch-up, generic lock-screen content, constrained endpoint transport, subscription generation/90-day lifetime, explicit logout revocation, worker recovery, tombstones and real source deep links. Manager summaries reuse authoritative counts and existing reports without another LLM. Six implementation slices and a separate full Phase 5 exit define controlled versus native/push acceptance and fixture cleanup.

Desktop localhost can support the secure APIs without a public inbound server; a phone using plain HTTP LAN access does not receive the same secure-origin exception. Actual push display remains subject to browser/OS permissions and vendor/network availability, and Hermbot/Docker must be awake for new updates. No permission prompt, live subscription, delivery, dependency change, runtime implementation or fresh test run occurred for this research. Phase 5.4 and the Phase 5 exit remain pending. Broader connected-service coordination still follows full Phase 5; mobile remains 6A after 6, and all hosting/VM/evaluation deferrals remain unchanged.


## October 26, 2026 — Phase 5.4 implementation and controlled/native verification

Implemented [attention and notification delivery](PHASE5-ATTENTION.md#19-implementation-and-current-acceptance--october-26-2026) above unmodified Hermes. Migration 017 adds five record groups and transactional source-change hints, independent projection/delivery pumps, exact current-state/source readers, versioned read state, encrypted browser registrations, constrained standards Web Push, owner preferences/quiet/DST/burst/overflow behavior and logout/retention handling. Attention is beside Routines; notification settings are in the owner menu. [Focused review and corrections](PHASE5-ATTENTION-REVIEW.md) includes transport timeout regression, scoped digest membership, worker coupling/lock order, source inhibition, canonical occurrence/file identity, browser generation fences and visible review errors. All existing dependency pins are preserved.

638 cumulative backend tests, lint and production build pass. Real-browser responsive checks and a headless service worker pass; scoped taste-inspector allowances are documented. Upstream integrity remains 2,169 unchanged Python files and nine preserved bot volumes. One real native group, `Phase 5.4 attention acceptance 9833c8d7160a`, contains a completed Sage-managed demonstration. The actual owner answer resumed the same native session after restart; two tool-backed contributions/artifacts were read and accepted. Proof: `test-results/phase54-live.json`; final controlled results and UI/integrity files use `phase54-*`.

The owner subsequently enabled notifications for Chrome and confirmed that notifications now work; the exact site-level versus OS setting was not specified. At the owner's request, temporary diagnostic controls, browser checks and receipt reporting are removed; normal push, registration, preferences and the original test button remain. See C64 in [ISSUES.md](ISSUES.md). Do not mark all of 5.4 complete from this report alone: closed-tab current-result navigation and fixture cleanup remain separate checks. The one completed group/paused routine/scoped grant remain for that check; `tests.attention_acceptance verify/notify/cleanup` has guarded commands and never supplies an owner answer or grants OS permission. Preserve owner registrations/preferences/keys. The overall Phase 5 exit remains separate. Connected-service coordination, broad evaluations/backlog fixes, hosting/VM work and Phase 6A placement keep their existing ordering/deferrals.


## October 26, 2026 — Phase 5 composed exit in progress

The owner said “do it” to the remaining 5.4/Phase 5 acceptance and cleanup. The full current backend suite passes 638 tests; two worker tests and lint pass. A new isolated exit group `Phase 5 exit acceptance 187381303ad0` has two completed native manager runs (one actual schedule, one signed local HTTP event), four tool-backed accepted child contributions/artifacts, exactly one final report per run, and two verified duplicate deliveries without extra work. API/task/event/notification worker restart preserved captured active native identities. The previous actual 5.4 owner-answer delay was revalidated rather than simulated or repeated. Seven fixture tasks completed with no connector operation. Future fixture schedule/trigger/source are archived; original owner groups/bot configuration/active routine definitions remain preserved. Fresh integrity checks verify 2,169 unchanged upstream files and nine preserved private volumes.

The owner confirmed Hermbot tabs closed with Chrome running. A subsequent fresh local 7×8 task completed, and its exact result push was accepted: attention `76a6dd98-e13f-4a89-a323-e361b939cd54`, delivery `5ffdbfa6-5bfb-46e7-b5d5-b18a129d9ab9`, task `a7ca4fcb-277a-44ea-b7c2-56ed528fd049`. The pending owner question asks whether clicking the newest notification opens “Closed-tab delivery check” and 56 in that exit group. Do not infer the answer from acceptance/read state alone. On actual confirmation, record its provenance, run guarded exit cleanup first (its baseline includes the retained 5.4 group), then `tests.attention_acceptance cleanup`; preserve actual subscriptions/preferences/keys. Keep 5.4 and Phase 5 unchecked until that evidence and cleanup exist. Resume the same proof, never recreate these fixtures. Evidence: `phase5-exit-live.json`, `phase5-exit-final.json`, `phase5-exit-backend.xml`, `phase5-exit-integrity.json`; detailed review is in `PHASE5-ATTENTION-REVIEW.md`.

Connected-service coordination remains next only after Phase 5 is fully closed. No broad evaluations/backlog fixes, Phase 6, mobile 6A, VM migration or hosting were started.


## October 26, 2026 — Phase 5 completed for local scope

The owner confirmed the newest notification, generated after their actual closed-tab readiness, opened the exact “Closed-tab delivery check” result containing 56. The confirmation is recorded verbatim with the target/delivery/task identities in `phase5-exit-live.json`. This closes the real browser gate without simulated permission, delivery or clicks. Guarded composed-exit cleanup ran first, then the retained 5.4 cleanup; both passed. Both fixture groups/routines are archived, the exit event trigger/source is archived and its signing secret cleared, and both scoped grants are revoked. Historical work/artifacts/decisions remain preserved. Final checks confirm seven original groups, unchanged original bot configuration, the original active routine definition, live browser registration/preferences and healthy notification worker; nine private volumes and all 2,169 upstream Python files remain intact.

Phase 5.4 and the separate Phase 5 exit are now checked complete for the owner-approved local scope. Final current cumulative result is 638 backend tests plus two service-worker tests; Ruff passes, the unchanged production build and responsive browser checks remain verified. Fresh composed scheduled/event runs, duplicate delivery and active-attempt restart identity checks pass. The actual prior owner-answer delay was revalidated rather than repeated, and the fresh signed local HTTP event is explicitly distinct from the prior live five-provider acceptance. All limits are retained in `phase5-exit-final.json` and `PHASE5-ATTENTION-REVIEW.md`. Earlier pending statements above are chronological history, superseded by this completion.

Next agreed implementation project: [connected-service work coordination](CONNECTED-WORK-COORDINATION.md), after this Phase 5 closure. It has not started. Broad evaluations/backlog fixes, Phase 6/6A, hosting and VM work retain their recorded sequencing/deferrals.


October 26 notification repeat: at the owner's request, repeated the real closed-tab exact-result push using one fresh local 8×9 task in temporary group “Notification retest e1665c48”. The actual owner confirmed tabs closed, then replied “done” to the exact report/72 verification request. Recorded successful actual display/navigation and archived only that temporary group; all seven original groups and enabled browser notification settings remain preserved, with zero active test attention. Evidence: `test-results/notification-retest-20261026.json`. No code/settings changes; Phase 5 stays complete and connected-service coordination remains the next pending project.


## October 27, 2026 — Connected-service coordination deferred again

After revisiting the saved coordination design and its two problem statements, the owner said “well do this later, what's next”. Keep the research and proposed solution; do not begin coordination implementation or assign a new date. Phase 5 remains complete. The next main-roadmap milestone is Phase 6.1 voice, starting with detailed planning: dictation/editable transcripts/explicit submission, spoken replies and voice memos, then live conversation/interruption and durable records through existing intake/authorization. Speech services, retention, latency and cost choices remain unsettled; no voice implementation has started. Phase 6A mobile remains after Phase 6, and existing evaluation/backlog/VM/hosting deferrals remain unchanged.


## October 27, 2026 — Voice held and demonstration teaching researched

The owner said “let's keep 6.1 at hold and move forward with 6.2” and requested extensive planning with deep internet research into similar implementations and broader software-system problems. [PHASE6-TEACHING.md](PHASE6-TEACHING.md) records current-source and pinned-runtime read-only inspection, primary-source implementation references (ShowUI-Aloha parser/log/trace code, Chrome Recorder, Playwright, Selenium, OpenAdapt, rrweb, WebRobot), 2026 research, Agent Skills/Hermes compatibility and broader provenance/idempotency/outbox/deployment patterns. References are distinguished from adopted dependencies or independently verified vendor claims.

The proposed delivery is admitted desktop input plus selected keyframes, explicit privacy exclusion before capture, reviewed immutable evidence, a bounded image/text-to-draft job, task-scoped candidate consultation through existing supported middleware, ordinary Hermes execution tests and reviewed native Markdown publication/refresh/rollback. Current secure input is ordinary VNC key traffic; a future recorder needs an acknowledged privacy barrier and late-frame fencing. Current skill-ref/capability checks require a teaching-specific test integration; pasting draft instructions is not claimed as native installed-skill testing. The final native install must be read back and demonstrated separately. No source patch, macro engine, training stack, automatic global candidate enablement or connection-based native-tool restriction is proposed.

The plan includes state/API/data/retention contracts, bounded resource proposals, failure/concurrency cases, three real demonstrations and seven implementation slices starting with feasibility. Recorder protocol/geometry/privacy behavior, the pinned middleware contract, actual vision-provider support, resource limits and retention/encryption details need validation before dependent implementation. Read-only planning changed documentation only: no native task, model generation call, recording, dependency installation or runtime/skill/configuration change was made. Phase 6.2 remains unimplemented; Phase 5 stays complete, 6.1 is held, 6.3/6.4 stay pending, mobile remains 6A, and connected-service coordination/evaluations/backlog/VM/hosting deferrals remain intact.


## October 27, 2026 — Phase 6.2 implementation

The owner authorized implementation of the researched 6.2 plan, then explicitly excluded `architecture/`. Demonstration capture, private-input barriers, encrypted private storage, evidence/draft revisions, bounded model generation, task-scoped candidate consultation, exact tested publication and retention UI are implemented locally. See [implementation/review](PHASE6-TEACHING-REVIEW.md). The final full backend run passed 654 tests; All 16 teaching tests and earlier regressions are included in the final 654-test run. UI fixture checks at 1440/768/390/320 px and isolated native packet/image checks passed. Underlying Hermes Python hashes, seven groups, configurations and nine original volumes were preserved. Phase 6.2's milestone remains unchecked: real owner recording, exact review/generation, fresh-input/negative/collaborative tests and installed native skill use are pending.

Automatic approval review rejected automated typing into a live bot desktop; the script did not run. Use owner actions for the live demonstration; do not rerun or indirectly execute that rejected script. Safe alternatives used intercepted UI data and disposable network-isolated containers with no owner volumes. A local owner fixture was prepared at `file:///workspace/hermbot-teaching-62-fixture/records.html` in Scout; no live browser navigation/input was performed. A generated-image probe established image processing (5/6 bands, not perfect recognition) and corrected the provider token-limit parameter mismatch. No owner evidence was sent in probes. Preserve all earlier phase holds and deferrals.
