# Skills, tools and handoff reliability

Status: implemented and verified locally on October 10, 2026. Authorized by the owner: plan extensively and implement the skills/tools improvements discussed after automatic work-item routing. Phases 1–3 remain complete; Phase 4 private-write acceptance remains a separate outstanding gate.

Architecture amendment, October 10, 2026: the owner requires a **harness independent** application and an unmodified underlying harness, with separate installable adapters and no application backend changes when adding a supported harness. The implementation and evidence below describe the existing build; its custom core `tool_schemas` seam and other Hermes source patches do not comply with the new requirement. Their replacement through existing supported interfaces remains pending. The architecture boundary in [README.md](README.md) and owner requirement in [CONTEXT.md](CONTEXT.md) take precedence over the historical source-patching approach below.

Initial owner update, October 11, 2026: harness independence and the generalized adapter platform were deferred. Continue with Hermes only. The earlier unmodified-Hermes rule remained active; public web research alongside connected applications became the immediate technical follow-up. The subsequent implementation below replaces the source patches and connection fence.

Subsequent owner authorization, October 11, 2026: remove connection-based blocking of all native tools. The current build removes that policy from capability discovery and execution, including historical root-task latches, revoked connections and delegation. It also removes all four upstream source patches. Schema projection now uses existing `llm_request` middleware with injected framework identity; raw skill reads use existing `tool_execution` middleware; owner catalog reads use the private adapter. Managed runtime code/plugin files are protected while private workspaces and state remain writable. Connector credentials, scoped access and review enforcement remain in the backend. Independently authenticated native actions are outside connector review coverage. The proposal and original evidence below are historical wherever they describe native connector restrictions or source patches.

## Problem and intended behavior

The owner sends an ordinary message. Conversation routing determines discussion, assignment, revision or status. For work, the responding model chooses relevant enabled skills and the tools/connections needed for the requested outcome. It checks access before execution and consults the selected procedures. Delegation supplies a concrete outcome and only explicitly shared inputs. The composer requires no skill or work-context selection.

Findings before the October 10 upgrade:

- Runtime toolsets advertise file writing, terminal, browser and skill management even when connection policy or conversation intent blocks their execution.
- Teammate discovery reports declared expertise without an effective inventory, so a manager can choose a helper unable to execute its proposed workflow.
- The three core bots have a small enabled skill set containing acceptance procedures. Installed, enabled, relevant and executable are different states. An enabled skill can require a blocked tool; enabling it does not override policy.
- Skill viewing supports template and inline-shell preprocessing in pinned Hermes. Under connection restrictions, a nominally read-only skill view must not execute that preprocessing.
- Handoffs accept prompt, dependencies and artifact IDs but cannot explicitly transfer owner attachments or describe the expected result in a structured field.
- Missing access usually appears only as a rejected tool call. The model needs actionable blockers before it attempts work, and the product must not report blocked work as successful.

## Architecture and boundaries

### 1. Effective capability inventory

Add a shared backend module computing capabilities from workspace permissions, bot configuration and revision, probed native inventory, installed enabled skill descriptors, current task intent, management grant, computer control and the existing connector policy. Runtime probes record bounded tool names and skill metadata, never credentials or private memory. A current-bot context exposes its authorized connections/actions/resources; teammate discovery exposes only planning metadata, without credentials, connection secrets or skill bodies. Declared expertise remains descriptive and is not proof of tool access.

Keep full installed tool inventory separate from tools available for the task and tools currently admitted at its preparation stage. Missing or incompatible inventory must be explicit. Administrative projections provide readable availability and restriction reasons. Active policy is rechecked at tool execution; cached discovery never grants authority.

### 2. Automatic preparation and skill consultation

Add `hermbot_prepare` with bounded selected skill names and requested tool/connector requirements. The actual responding model selects skills semantically from authenticated descriptors, including relevant explicit owner references; no keyword classifier or second model is introduced. Empty selection is valid when no enabled procedure fits. Disabled skills remain disabled and enabling them remains an owner setting.

Validate requirements against effective access and record a per-attempt preparation result, selected skills, content hashes and typed blockers. Return the selected native skill instructions as bounded raw text, without template or shell preprocessing. Use immutable receipts so a repeated call ID cannot execute or consult a different request. Reprepare after a new native continuation or changed capability facts. Skills are guidance and cannot grant tools, credentials, management authority or external write permission. Skill bodies do not appear in teammate discovery or durable public preparation events.

Before preparation, permit routing and limited inspection/discovery, skill reading, owner input and finishing an honest blocked response. Execution tools and operational Hermbot actions require a successful current preparation in the running plugin. Backend authorization remains authoritative. If all paths remain blocked, settling must record failure rather than successful completion. A model may revise its plan to use available alternatives; missing-access requests should name only the required capability and affected bot/resource.

### 3. Accurate model-facing tool schemas

The pinned Hermes plugin API supplies execution middleware and turn context but no public schema-filter callback. Introduce an additive generic `tool_schemas` middleware surface, called by a guarded request-assembly seam before cache markers are computed. The Hermbot plugin implements that surface. Filter a request-local copy; preserve canonical schemas, system prompt, transcripts, correlation IDs and provider behavior. Stable framework session/task identity comes directly from the assembling agent, never model arguments or state set in another hook's copied worker context. Admission failures expose no executable tools. Hermes deployments without a registered schema middleware remain unchanged.

Apply the same effective capability result to request schemas and execution middleware. Keep conservative connector restrictions, including revoked policies and collaborating task trees. Do not enable browser/terminal bypasses. Suppress forbidden native tools instead of inviting repeated rejected calls. Preserve human computer takeover fencing and routed conversation restrictions.

### 4. Reliable handoffs

Extend handoffs with a bounded expected-output brief, optional recipient skill requirements and explicit attachment IDs. Validate target enabled skills before admitting the handoff. Validate artifacts through existing task access and attachments against the caller's granted owner attachments and conversation. Never inherit private memory, browser state, credentials or arbitrary sibling files. Persist a bounded handoff context including the public work objective, expected output and input metadata; stage shared attachments through the existing private runtime installation path. Preserve root/grant identities, asynchronous completion, dependency wakeups, receipt idempotency and reassignment semantics.

Each recipient performs its own preparation using its actual capabilities. Managers discover effective target capabilities before choosing a specialist. A connection policy acquired by any participant still fences the entire task tree; delegation cannot recover forbidden native execution.

### 5. Simple owner experience and evidence

Remove the normal-composer skill-reference picker; retain owner administration for enabling and reviewing procedures. Memory & skills shows available capabilities and restriction explanations alongside installed/enabled skills. Task inspection shows selected skills, requirement blockers and handoff inputs/outcome. Do not add a new context picker or a blanket permission approval. Existing forms remain the place to change bot configuration and connection access; the bot explains the exact missing capability in chat.

Durable preparation evidence includes selected names/hashes and blocker categories without skill bodies or credentials. Existing native event/receipt evidence remains the source of truth for tool execution. Availability does not claim that a provider accepted an action; real connector execution and exact human approvals remain separate.

## Implementation sequence

1. Migration and pure capability projection, requirement validation and bounded inventory contract.
2. Native metadata/probe integration and preparation tool with safe consultation and immutable receipts.
3. Plugin preparation admission, request-local schema filtering and guarded pinned-source compatibility checks.
4. Handoff fields, permission validation, staging and continuation/reassignment preservation.
5. Composer simplification, readable owner capability/skill diagnostics and task inspection.
6. Focused regressions, full isolated backend suite, lint and production TypeScript/Vite build.
7. Idle-only deployment of app and runtime images; upgrade the three existing bots through normal provisioning, preserving all private volumes, credentials and owner-created groups.
8. Real native acceptance and desktop/mobile checks. Review implementation, correct findings, rerun relevant checks and archive only the acceptance fixture.

## Acceptance and review cases

- A normal discussion/status turn creates no work and needs no execution preparation.
- A real work request chooses a relevant enabled skill without composer references and consults its exact raw instructions; an irrelevant enabled skill is skipped.
- A disabled, missing or incompatible skill is not silently enabled or used.
- A required blocked tool produces an actionable blocker before execution; an alternative available plan may proceed.
- Native terminal/browser/write schemas disappear for restricted task trees while permitted read/skill/memory and authenticated Hermbot operations remain usable at the correct stage.
- Skill template/inline-shell content never executes during restricted consultation, including native `skill_view` calls.
- Preparation is invalidated across attempts, configuration/policy changes and delegation that tightens the task-tree fence. Old receipts do not erase newer preparation state.
- No preparation, capability projection or handoff expands grants or connector access. Revocation, human control, conversation-only restrictions and unresolved external-outcome fences remain effective.
- Handoffs explicitly stage granted attachments and artifact versions, carry expected output, and keep private/foreign inputs inaccessible. Missing recipient requirements admit no child and consume no handoff budget.
- A blocked plan does not settle as successful work. Completed collaboration waits for actual child reports and combines only granted output evidence.
- Owner diagnostics expose no credentials, private memory or unrelated skill bodies. Desktop/mobile have no new context controls, horizontal overflow or script errors.
- Real acceptance uses local artifacts/attachments and controlled native capability checks; it does not send external messages or perform provider writes. Phase 4 reviewed-write tests remain outstanding and are not counted as passed here.

## Deployment and recovery

Migrations are additive and preserve existing execution/history identities. Existing work is never replayed to populate capability metadata. Readiness requires the new tool and adapter capability version before new dispatch. Upgrade idle runtimes through normal bot provisioning and retain all nine core private volumes. If a new build fails readiness, keep work undispatched and correct the image rather than bypassing admission. Reconcile native attempts and archive only settled acceptance groups; preserve owner-created chats, groups, files and provider connections.

## Completion evidence

The cumulative isolated backend suite passes **265 tests**, including 26 focused capability/history regressions (`test-results/backend.xml`, `tests/test_execution_capabilities.py`). Ruff and the production TypeScript/Vite build pass. Migration 009 is applied; all three existing runtimes have the new 18-tool Hermbot contract and capability adapter. App/runtime builds pass their guarded pinned-source compatibility checks.

`test-results/skills-tools-native.json` records four actual model cases: automatic relevant skill selection with an irrelevant enabled procedure skipped; discussion without work/preparation; exact unavailable-terminal reporting with failed logical work and a normally completed native response; and an explicit attachment handoff with recipient consultation/read/publish followed by a resumed manager reading the actual artifact and publishing the combination. No external application tool was used. Request visibility evidence shows forbidden native command/browser/write schemas absent. Native wrapper verification preserves the inline shell example as raw text and confirms its marker file was never created.

The first integration run exposed that hook-local ContextVars do not propagate out of Hermes's copied timeout-worker context. Its task identity is retained in the proof's `integration_failures`; the corrected generic middleware passes native acceptance. Review also corrected absent/stale preparation, unclassified-response completion and retryable native history availability during refresh. See [SKILLS-TOOLS-REVIEW.md](SKILLS-TOOLS-REVIEW.md) for findings, corrections and practical limits.

`test-results/skills-tools-ui.json` and desktop/mobile screenshots verify the simplified composer, readable availability/restriction explanations, consulted skills and shared attachment/expected-output inspection, with no horizontal overflow or JavaScript errors at 1440 and 390 pixels. The four temporary procedures are removed, original enabled selections restored and the acceptance group archived through normal owner APIs. Saved records and reports remain for inspection. No owner group, private volume or connection was removed.

The earlier automatic work-item browser flow passes again using real discussion/revision/status turns, hide/restore, reload and both viewport sizes. The saved Google/Slack/GitHub read orchestration verifies without a new external call. Read-only closure audits pass: three healthy current-image runtimes, nine private volumes, the two owner-created groups, original enabled skill selections, no unfinished work or new pending connector reviews, and actual staged attachment contents plus checksummed recipient/combined reports containing every supplied fact. Evidence: `test-results/skills-tools-completion-audit.json` and `phase4-implementation-audit.json`. Phase 4's private reviewed-write gate remains explicitly pending.
