# Hermbot work outcomes and human requests

Status: implemented locally on October 11, 2026, with code review, regression checks, browser checks and real-model acceptance. The design below is retained as the implementation reference; [WORK-OUTCOMES-REVIEW.md](WORK-OUTCOMES-REVIEW.md) records the shipped behavior, evidence and limits. Implementation and acceptance did not authorize or send an external message.

## 1. Problem and scope

The Kaggle group exposed three connected failures. Bots found a Slack member and the owner confirmed the person, but they still demanded an existing DM conversation ID. An information request appeared as an approval, so clicking the button supplied the word `once` instead of an answer. Finally, the work card showed completed even though the requested DM had never been proposed or sent.

The code before this change explained these failures:

- [connector_catalog.py](../backend/connector_catalog.py) gives `slack.post` and `slack.history` generic string arguments without explaining their different target semantics.
- [models.py](../backend/models.py), [app.py](../backend/app.py), and [App.tsx](../web/src/App.tsx) implement review decisions but have no ordinary typed answer contract.
- [worker.py](../backend/worker.py) can settle an execution after a textual result, and [work_items.py](../backend/work_items.py) projects the current root execution's status onto the work item. Neither checks all requested results.
- The worker's older instruction against native browser/terminal writes conflicts with the connected-tool guidance. This leaves bots with inconsistent policy descriptions.

The solution must cover research, files, code, delegation, and connected applications. Keep the existing automatic association of messages with work. Discussion, progress questions, and ordinary answers must remain natural conversation; users should not classify messages or choose a context dropdown.

Hermes remains the sole harness and its source stays unchanged. Implement through Hermbot's backend, frontend, private adapter, and supported plugin interfaces. Preserve enabled native tools, existing connector permissions, exact approvals, standing permissions, private bot state, management grants, budgets, and human computer control. A universal harness platform is outside this proposal.

## 2. Separate the records that mean different things

| Record | Meaning | Completion evidence |
|---|---|---|
| Work item | The user's request across messages and bot turns | All active required outcomes fulfilled |
| Required outcome | One requested result, such as a report or a Slack message | Evidence appropriate to that result |
| Task and execution attempt | A bot's assignment and actual runtime activity | Execution lifecycle; this alone does not fulfill an outcome |
| Connector operation | A specific external action with a frozen payload | Existing dispatch state and provider receipt |
| Human question | Information needed to continue | An authenticated, validated answer |
| Approval | Permission for an exact proposed change | Owner decision or matching standing permission |

Extend the existing work item rather than introduce another top-level workflow system. A request such as “research competitions, make a comparison table, and DM it to me” remains one work card with its required results underneath it. If the owner adds delivery later, append that requirement to the same work item while preserving the completed research.

Execution can finish while work awaits an answer, approval, access, or reconciliation. A helper finishing successfully means its assignment finished; it does not prove that the overall request was fulfilled.

## 3. Record what the user expects

During existing semantic routing and preparation, the acting bot records a bounded, structured set of required outcomes before carrying out an assignment. Include a concise description, kind, originating owner message, work revision, responsible actor, dependencies, target if applicable, and completion criteria. Initial kinds should cover a response or report, an accessible artifact, a connector effect, and execution checks such as a test run.

Use the current model and conversation context. Avoid keyword classification, a compulsory second planning model, or a universal model judge. The backend validates the structure, references, authorization, and evidence; it cannot prove that the model interpreted every sentence correctly. Show the understood request and its results compactly in the work card so the owner can correct an omission in ordinary chat. Evaluate extraction with real multi-turn examples.

Requirements persist across turns. A follow-up can add or revise them; a short final response cannot silently discard unfinished requirements. Agents cannot downgrade an owner-required result to optional or remove it to obtain completion. Changes need provenance in an owner instruction. An optional helper output does not prevent completion, while an unmet required delivery does.

Version outcomes individually and associate them with a work revision. Unchanged completed results remain valid when another result is added. A changed recipient or message invalidates a pending draft based on the old payload. A message already sent remains a historical effect; changing the request after sending requires a new, explicitly requested action rather than rewriting the receipt or automatically sending again.

Serialize requirement changes, answer continuation, completion, and dispatch checks using the existing lock order and expected revisions. An older task cannot fulfill a newer requirement by submitting a stale result. If a write has already begun when its requirement changes, retain and reconcile that operation before considering replacement delivery.

Discussion and status turns create no new requirements. A message answering a pending question can resume its existing work without creating a second work item. If the association is genuinely ambiguous, ask one concise clarification in the conversation.

## 4. Give tools usable action contracts

Enrich the existing fixed connector catalog with descriptions of accepted target kinds, required inputs, known preconditions, success fields, and recovery behavior. Distinguish an available action from an enabled connection, sufficient resource permission, and information still needed from the owner. Catalog descriptions explain behavior; they do not grant authority or allow model-selected URLs or HTTP methods.

Version action guidance and include it in existing preparation fingerprints. Refresh context when the deployed action contract or connection changes so a persistent bot does not keep acting on obsolete instructions. Skills can suggest procedures, but current authenticated tool contracts determine supported behavior and backend permissions determine access.

For Slack, explicitly explain that `slack.post.channel` can be a workspace member ID for a bot-to-person DM, while history reads require a conversation ID. Slack documents that supplying the intended member's ID to `chat.postMessage` opens the DM if necessary and returns its conversation ID. This route does not require a separate `conversations.open` action. An app cannot use a DM between two other people as its own delivery route. [Slack chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/)

Keep `slack.post` and its existing authorization identity. Normalize and validate target semantics in its adapter rather than silently adding an automatically enabled DM permission. Channel-only resource restrictions continue to restrict posting; a member ID must not bypass them. Any future separate DM permission would require explicit owner configuration.

Record resolved targets with provider, workspace or account, entity kind, stable identifier, display label, discovery source, and owner confirmation where needed. A confirmed member ID should be reused within that request. Do not keep searching other people's DM history for an unnecessary conversation ID. Do not interpret “me” as the bot token's identity or reuse identities across accounts.

Use authorized lookup capabilities when available. If several people match, ask the owner to choose. If no usable identity is known and no lookup is available, request the smallest missing piece of information. Exact IDs already supplied and confirmed do not require additional discovery scopes. A lookup failure, insufficient access, unsupported behavior, and an ambiguous target should produce different explanations.

Resolve targets and prepare previews without external mutations. Creating a conversation as a side effect must not become an unreviewed lookup step; the supported member-ID post performs that creation within the approved write.

## 5. Make questions and approvals separate interactions

Add persistent typed input requests, with a dedicated request/answer API and a supported Hermbot plugin operation. Each question records the work and outcome it concerns, originating task, question version or hash, expected answer type, expiry, status, and answer provenance. Support short text, choices, and entity confirmation first.

An ordinary question renders an answer field or meaningful choices with a send-answer control. An external action renders the exact destination and content with approve, edit, or reject controls. An answer confirming a recipient grants no permission to send a message. An approval supplies no missing answer text.

The answer endpoint authenticates the owner, locks the current question and task, validates a nonempty answer of the correct type, and checks version, expiry, cancellation, and work revision. Duplicate delivery of the same answer resumes the task once. A conflicting duplicate or stale answer is rejected clearly. Resume the actor that asked, with the actual answer and the original work association.

Waiting for a question releases execution capacity. It does not silently extend a grant or execution budget. Expired authority remains a separate blocker. Deduplicate equivalent unresolved questions about the same missing field and reuse information already supplied by the owner.

Maintain compatibility for `hermbot_review` callers by translating ordinary `kind=input` requests into the typed question flow. Keep secure computer input separate: passwords and sensitive form values are entered directly into the computer, never saved as ordinary answers or conversation text. Existing acknowledgement and human-control checks remain.

Previous clicks on `once` for a text question are legacy decisions, not answers and not authorization for a later external write. Do not migrate them into fabricated input values or message approvals.

## 6. Verify completion with evidence

Extend `hermbot_finish` with structured outcome results and evidence references, alongside its human-readable response. A bot can report fulfilled, blocked, or failed outcomes and explain why. The backend decides which claims are supported; the bot cannot mark an external effect successful merely by saying it happened.

For connector effects, reuse the existing durable operations and receipts. Bind each proposal to an outcome and its relevant revision before dispatch. Accept success only from a matching operation with the correct action, account, target, payload, actor authorization, and successful provider result. Reject unrelated receipt IDs, inaccessible evidence, and stale drafts. Pending, denied, failed, and uncertain operations cannot fulfill a required send.

For Slack, a successful provider receipt establishes that Slack accepted the message and identifies the channel and timestamp. It does not prove the recipient read it. Missing history access must not turn a successful send into a resend requirement. [Slack chat.postMessage response](https://docs.slack.dev/reference/methods/chat.postMessage/)

Artifacts must exist and be accessible to the intended recipient, with a stable version or content hash. A private filesystem path is not a shared deliverable. Test outcomes reference the actual run, scope, and recorded result. These checks establish artifact delivery or recorded test results; they do not establish that every research claim is true or that software has no defects.

Expose evidence strength accurately. Some qualitative work remains model-reported or owner-reviewed. Native browser and terminal tools remain enabled under existing controls; independently authenticated external actions are outside connector approval coverage. Record available native evidence without presenting it as a connector receipt or promising equivalent guarantees. If an external effect may already have occurred, preserve uncertainty and prevent automatic repeated delivery through another tool.

Do not infer success or failure from words such as “done” or “cannot” in generated prose. Do not require an extra model verdict for every action when existing backend evidence already establishes the result.

## 7. Carry requirements through collaboration

Delegation should reference the outcome IDs a helper is responsible for, its expected result, explicitly shared inputs, and relevant target information. Keep parent and child execution states separate from the results they contribute.

Helpers prepare their own authorized tools and relevant enabled skills. Manager permission does not transfer a teammate's connection credentials, private memory, or resource access. Evidence returned to the manager follows existing artifact and delegation sharing rules. Prefer a backend outcome confirmation containing only authorized result metadata when a manager does not have access to the underlying private receipt.

The parent resumes with fulfilled outcomes, remaining requirements, and precise blockers. It should choose a next step based on this state, rather than treating every child summary as complete or repeating discovery already settled by the owner. Optional helper failures and required helper failures have different effects on overall completion.

Do not automatically reassign a write after an uncertain result. Existing reconciliation, grants, deadlines, and cancellation rules continue to apply.

## 8. Derive clear work status and recovery

Derive product status from active required outcomes and execution activity. Keep scheduler enums unchanged where their meaning differs: the current scheduler's `blocked` can mean waiting for dependencies, which is not the same as a user-facing access blocker.

Unresolved external effects take priority over a completion claim, including effects from optional helper work. Completion requires all active required results and definitive settlement of started writes. A new assignment with no registered requirements stays in planning rather than passing an empty completion check.

| Situation | User-facing result |
|---|---|
| Work is executing | Working |
| Information is missing | Needs answer, with the actual question |
| Exact external draft awaits permission | Needs approval |
| Access or capability prevents progress | Blocked, with a specific reason and next action |
| Some results are ready and others remain | Show partial progress and the remaining action |
| An effect may have occurred but is unresolved | Needs reconciliation |
| Every active required result is fulfilled and started writes are settled | Completed |
| Execution ended without fulfilling requirements | Failed or blocked, according to the recorded reason |
| Owner explicitly stops the work | Stopped, preserving results and unresolved effects |

Use one compact card, for example “Research ready · Slack delivery needs approval.” Expand for individual results, evidence, participating bots, and action history. Show useful recipient names and workspace context in previews, with identifiers available as detail. Keep internal schema and database plumbing out of ordinary user flows.

A completed runtime turn does not remove a question or mark blocked delivery complete. A status question never starts a retry. Recovery should identify the smallest missing condition, preserve usable results, and continue the existing work only under valid authority. Avoid repeated bot questions and automatic token-consuming retry loops.

If delivery is uncertain, retain that state even when work is stopped or reassigned. An owner may stop future activity but cannot turn uncertainty into proof that nothing happened. Reconciliation precedes a replacement write. Safe read retries and writes conclusively known not to have happened can use bounded recovery under the existing rules.

Unify policy guidance used by the worker, authenticated context, and plugin descriptions. It must accurately describe enabled native tools and the connector approval boundary. This is a consistency change, not a new connection-based tool filter.

## 9. Build and roll out in increments

1. **Contracts and storage.** Add versioned outcomes, evidence bindings, typed input requests, and their APIs. Enrich connector metadata and converge policy text. Preserve existing operation dispatch, receipt, scope, and hash checks. Require negotiated support before accepting structured outcome claims.
2. **Answers and targets.** Implement question rendering, answer validation, one-time continuation, secure-input compatibility, target confirmation, and the Slack member-ID path. Show the exact recipient and payload in the existing connector review.
3. **Work completion.** Integrate outcome registration and revisions with routing/preparation, delegation, finish, and worker settlement. Replace latest-execution status projection with an outcome-aware product projection. Keep tasks and scheduler controls intact.
4. **Conversation and recovery UI.** Show partial results and precise pending actions in the existing card. Preserve automatic message association, correct chat answers, refresh behavior, and reconnect recovery.
5. **Acceptance and review.** Run meaningful backend and browser checks, then real-model local acceptance. Review authorization, concurrency, privacy, completion claims, recovery, and UI behavior; fix findings before marking this change complete.

Principal integration points are [work_items.py](../backend/work_items.py), [service.py](../backend/service.py), [worker.py](../backend/worker.py), [models.py](../backend/models.py), [app.py](../backend/app.py), [capabilities.py](../backend/capabilities.py), [connector_catalog.py](../backend/connector_catalog.py), [connections.py](../backend/connections.py), the [Hermbot plugin](../plugins/hermbot/__init__.py), and the [work UI](../web/src/WorkItems.tsx), [review UI](../web/src/App.tsx), and [API types](../web/src/api.ts). Use additive database migrations.

Historical work should retain its original record with legacy completion evidence clearly distinguished. Do not manufacture verified outcomes for old completed tasks. For the existing blocked DM, preserve the report and confirmed person; a future explicitly resumed delivery must create a fresh exact draft. Existing cancelled drafts stay cancelled. Updating the application must not automatically send that message.

Upgrade the supported plugin only when compatible and safe for active work. Preserve private volumes, bots, groups, connections, and owner settings. Do not restart busy runtimes or rewrite historical messages as part of migration.

## 10. Acceptance criteria

- A known, confirmed Slack member ID proceeds to an exact DM draft without requiring DM history or an existing conversation ID. An ambiguous person gets one meaningful choice; “me” is never inferred from the bot identity.
- A typed question receives the actual answer. Empty answers, generic approval clicks, expired questions, conflicting duplicates, and stale revisions cannot resume or authorize work incorrectly. Refresh and duplicate submission resume once.
- Research followed by delivery stays one work item. Discussion and status messages create no work. Research can finish while delivery remains pending; a later completed turn cannot erase that requirement.
- Connector success requires the correct backend receipt. Wrong targets, unrelated operations, inaccessible evidence, revoked permissions, edited drafts, and unmatched standing permissions cannot fulfill an outcome.
- A successful send does not require optional readback access, and never triggers a repeat because history is unavailable. A timeout or crash after a possible effect remains uncertain and requires reconciliation before any replacement send.
- Required child failures keep the overall result incomplete. Optional failures do not. A manager cannot borrow another bot's credentials or use a child's unrelated success as evidence.
- Owner revisions preserve unchanged completed results, invalidate affected pending drafts, and never cause an already completed external action to replay automatically.
- Secure values remain outside question records and chat. Human takeover, budgets, grants, task cancellation, and existing connector policy continue to work.
- Native tools remain available alongside connected apps, with consistent guidance. Source comparison confirms upstream Hermes remains unchanged.
- Real-model acceptance covers a single bot and a manager/helper workflow. Any real external delivery uses a fresh owner-approved exact draft; preparing a plan or migrating legacy work does not authorize a send.

The exit condition is a natural conversation in which bots understand the available actions, ask only for information they actually need, retain every active requested result, and show completion only at the strength supported by the evidence.
