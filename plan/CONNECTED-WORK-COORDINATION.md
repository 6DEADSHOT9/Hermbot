# Connected-service work coordination — post-Phase 5 plan

Recorded: October 26, 2026. Status: researched and planned; deferred again by the owner on October 27, 2026; implementation has not started. No new implementation date or milestone was selected.

## Owner decision and placement

The owner instructed: “we will work on this after finishing phase 5 completely. add this in the plan with the solution we resaerched and found for this.”

Finish Phase 5.3 recurring orchestration, Phase 5.4 attention/notifications and the Phase 5 exit gate before starting this work. This is a post-Phase 5 follow-up, not an added Phase 5 completion requirement. Existing phase numbering, Phase 6A mobile placement and deferred VM migration remain unchanged. The separate additional-service extensibility discussion is not started by this plan.

The existing evaluation-before-issue-backlog-fixes decision remains in force. This entry saves the coordination design and its focused acceptance cases; it does not start broad evaluations, authorize unrelated fixes or claim this problem is solved. Historical Phase 5.2 acceptance remains evidence for its tested scope, not proof of coordination across overlapping independent triggers.

## Problem and desired behavior

Several teammates may have access to the same connected account, and several triggers may match related input. Independent admission can create multiple work roots for one request. Provider redelivery, subsequent messages, cross-service notifications and ambiguous write outcomes add different kinds of repetition.

Apply the solution across Slack, Gmail, Calendar, Drive, GitHub and supported future integrations. Access to a service makes a teammate eligible; it does not assign every matching teammate responsibility for every event.

**One logical request within a defined automation purpose and workspace has one accountable teammate, with optional contributors.** Independent purposes may intentionally process the same event. Related valid messages can update existing work without becoming new assignments. Different work items continue in parallel.

Examples:

- Three support teammates eligible for one support request share one responsibility; they do not each start the same assignment.
- A support workflow and a security workflow may independently process the same GitHub issue.
- A follow-up changes the current request instead of being discarded as a duplicate.
- A confirmed issue creation may supply evidence to existing work; its notification is not automatically a new instruction.

## Research findings and their application

| Established approach | Finding | Application to Hermbot |
|---|---|---|
| Trigger.dev durable session routing | Verified webhook deliveries sharing a key can enter an existing session; independent subscribers still receive their deliveries. Webhook actions are distinct from chat messages. | Correlate input before creating work; keep independent automation purposes separate. |
| Camunda message correlation and aggregation | Multiple distinct messages can belong to one process. Correlation and duplicate-message identity serve different purposes. | Attach corrections, answers and evidence to existing work without dropping them. |
| Inngest layered idempotency | Event deduplication, consumer deduplication and protection of external effects are separate. Reused IDs can suppress legitimate new occurrences; documented deduplication windows are finite. | Define identity and retention separately at each boundary. |
| Consumer groups in Dapr | Consumers can share responsibility while independent subscriptions receive their own copy. | Group alternative handlers for one purpose; preserve intentional fan-out. |
| Inngest singleton policies | Concurrency limits do not eliminate duplicate runs. Skipping can discard work; cancellation does not undo completed effects. | Classify duplicate, follow-up, separate request and superseding update explicitly. |
| Restate keyed state | State changes can be serialized per key while independent keys proceed concurrently. | Coordinate per work item using short state transitions, without a global execution bottleneck. |
| Provider webhook contracts | Notification IDs, subscription IDs, resource IDs and actual change identities differ. | Keep provider-specific normalization and recovery semantics. |
| Workato integration-loop guidance | Several automations can repeatedly trigger each other; explicit provenance helps distinguish reflected changes. | Record causation and receipts, then suppress verified echoes without hiding subsequent human edits. |

The architectural family is a durable process manager with message correlation, shared work admission and idempotent effects. This is a recommendation inferred from these systems, not a claim that one vendor is universally best. Their patterns do not require installing their platforms or modifying Hermes.

### Recent agent-specific research

The September 24, 2026 preprint *Where Does Exactly-Once Live?* studies ambiguous writes across simulated services, models and harnesses. Immediate read-back can resolve some lost acknowledgements, but late commits and duplicate transport delivery require stronger tool contracts. Its controlled sandbox results support durable operation identities and provider-aware recovery; they do not establish production guarantees for Hermbot.

The July 11, 2026 preprint *Temporary Authority, Permanent Effects* studies authority becoming stale before a durable action. It supports rechecking current authority at dispatch and preserving the binding between approval, payload and work revision. Its conditional experimental findings do not authorize adopting its proposed runtime or patching Hermes.

## Proposed solution

### 1. Preserve distinct identities

Persist these independently, retaining provider and account/workspace boundaries:

| Identity | Meaning |
|---|---|
| Delivery/event identity | A particular provider occurrence, including its redeliveries where the provider contract permits identification. |
| Resource identity | The message, issue, document, calendar object or other entity involved. |
| Work/correlation identity | The logical request or automation occurrence within a purpose and workspace. |
| Operation identity | One intended external action associated with the admitted request and approved payload/revision. |

The provider adapter must establish occurrence identity from its documented contract. A local replay identifier or credential-record ID is not a universal business identity. The same resource can change repeatedly; resource ID alone cannot identify duplicate changes. Never merge private work across account or workspace authorization boundaries.

Cross-service correlation requires evidence such as explicit references, known resource relationships or recorded causation. Similar text alone is insufficient. The same person may legitimately request the same action again. If the relationship cannot be established, preserve separate identity and use ordinary clarification when necessary.

### 2. Admit shared work before selecting executions

Use the durable inbox and a database-enforced claim to atomically create or find the work for a request and automation purpose. Record the event-to-work association before dispatch; where acknowledgements are required, acknowledge after durable receipt rather than waiting for agent execution.

Alternative handlers for one purpose must share the claim. Candidate bot IDs and overlapping trigger IDs must not split that shared responsibility. Distinct independent automation purposes retain separate claims. Define how existing trigger definitions identify these cases during implementation; do not silently merge all legacy triggers.

Reuse the existing work item, outcome ownership, contribution and controller records. Read-only inspection found independent per-trigger admission in `backend/triggers.py`; shared ownership after work creation does not prevent multiple initial roots. Recheck the current code and schema when implementation begins.

### 3. Correlate and interpret incoming input

After provider normalization, determine whether input is a duplicate, an existing-work update, a new request, an independent automation occurrence or synchronization-only information. Preserve distinct valid events even when they concern one resource.

Gmail history positions and Calendar watch notifications require provider-specific change retrieval; they are not individually equivalent to user instructions. Define coalescing per event type only when using the latest state preserves the purpose of the automation.

Reuse automatic message association and work-intake behavior. Do not require a context selector on every message or treat every external message as an assignment. Saved owner configuration supplies authority; arbitrary external text must not be submitted as an authenticated owner command.

### 4. Retain one accountable owner with explicit collaboration

Use existing assignment, eligibility and permission mechanisms to select the responsible teammate, then retain ownership across follow-ups and worker restarts. Contributors can research or produce artifacts without becoming competing final responders. Define reply responsibility for each work/destination rather than forbidding every legitimate contribution or intermediate update.

A worker lease expiring is not evidence that the logical owner failed or that an external action never happened. Reassignment must reconcile existing execution and pending effects. Bind dispatch to current ownership and work revision so a stale worker cannot enqueue newly unauthorized actions. Short database transitions protect shared state; never hold a database transaction across a long model/tool call.

### 5. Protect external effects separately

Extend existing connector operation and receipt handling. The backend owns stable logical operation identity across retries, restarts and delegated attempts; assigning each attempt a fresh key defeats this protection. Independent model proposals of the same action must resolve against the shared work/operation records rather than relying on text similarity.

Use provider idempotency keys where supported and documented. Retain successful receipts if later task reporting fails. If a write may have succeeded, use supported read-back/reconciliation before another attempt. An unresolved result stays uncertain; a timeout does not establish safe replay.

Changed intent or payload requires a new appropriate operation revision and authorization; do not reuse an idempotency key for a different action. Recheck current connection access, ownership, work revision and exact review or standing permission at dispatch. Ownership does not grant new connector authority.

### 6. Track causation and prevent cross-service loops

Retain links from work to operations, receipts and observed provider changes. Suppress a correlated reflected effect when it would unintentionally restart the same automation purpose. Do not blanket-ignore bot-authored resources, all activity under the owner's account or all future changes to an object. Preserve genuine later edits and independent purposes.

Record why an input was attached, admitted independently or suppressed. Expose useful work, ownership, handover and uncertain-operation state through existing inspection/history surfaces without adding mandatory per-message controls.

## Implementation sequence after Phase 5

1. Reinspect existing admission, work identity, ownership, controller, intake and operation code. Define provider identity contracts, purpose grouping, retention and migration for saved triggers. Establish focused failure cases before changing behavior.
2. Add atomic shared admission and event-to-work association using the current PostgreSQL-backed architecture. Preserve existing trigger history, receipts and independent purposes; do not replay historical events during migration.
3. Integrate automatic correlation, owner selection, contributions and handover through existing work mechanisms. Preserve external-sender trust boundaries.
4. Connect operation identity, current-authority checks, reconciliation and causation across supported providers. Keep unsupported guarantees explicit.
5. Run focused concurrency/recovery checks, appropriate live provider acceptance and affected regressions; review/fix findings and record evidence and limits before claiming completion. Live external writes retain the existing exact-action approval or standing-permission policy.

This sequence is proposed work, not an implementation report. Reuse current helpers and database state before adding infrastructure. No global always-running manager agent, second workflow framework, additional harness, Hermes source patch or connection-based native-tool blocking is required.

## Acceptance cases

- Repeated delivery produces one work item per intended purpose and does not repeat a completed effect.
- Concurrent overlapping handlers for one purpose create one root and one accountable owner; distinct configured purposes both run.
- A follow-up, correction, question answer or status request associates correctly without unwanted new assignments or discarded input.
- A later genuine edit to the same resource is preserved; identical wording in a fresh request is not automatically suppressed.
- Related events from different services associate only with established evidence; unrelated accounts, workspaces and requests remain separate.
- Worker/API restart preserves work identity, ownership and recorded outcomes. Duplicate wake-up does not transfer ownership or repeat effects.
- A handover rejects stale-owner dispatch; changed requirements, revoked grants and superseded approvals cannot authorize new writes.
- A successful write with a lost response is reconciled where the provider supports it. An unresolvable result remains visibly uncertain instead of being blindly retried.
- Cross-service reflected changes do not form unintended loops; subsequent human changes and independent automations still run.
- Conversation replies have an accountable sender while authorized contributor actions and independent work can proceed.
- Existing routines, event history, daily limits, review flows and successful receipts survive migration and regressions.
- Verify Hermes source remains unchanged and no connection-based native-tool blocking is introduced.

## Limits and decisions to settle during implementation

Define initial automation-purpose grouping, legacy trigger migration, identity retention, supported correlation evidence, handover policy and provider-specific recovery capabilities. Validate these against the deployed provider/API versions. Do not create a generic adapter platform merely to save this plan.

No universal end-to-end exactly-once guarantee is possible for arbitrary external actions. Provider contracts determine available idempotency and reconciliation. Hermbot cannot enforce its connector operation ledger over actions performed through independently authenticated native browser/terminal tools; preserve that existing coverage limit and keep owner-enabled tools available.

## Research sources

Sources reviewed for the October 26, 2026 discussion. Recheck evolving documentation before implementation. These are architectural references, not selected dependencies.

- [Trigger.dev: routing webhooks to durable sessions](https://trigger.dev/docs/webhooks/session-routing) — session correlation, independent subscribers and provider versus local replay identity.
- [Camunda: message aggregation](https://docs.camunda.io/docs/components/concepts/message-aggregation/) and [message concepts](https://docs.camunda.io/docs/8.10/components/concepts/messages/) — process correlation, distinct message IDs and bounded deduplication.
- [Inngest: idempotency](https://www.inngest.com/docs/durable-execution/guides-and-advanced/idempotency) — event, consumer and external-effect boundaries, key scope and finite windows.
- [Inngest: singleton](https://www.inngest.com/docs/durable-execution/flow-control/singleton) — differences between concurrency, skipping, cancellation and completed effects.
- [Dapr: publish/subscribe overview](https://docs.dapr.io/developing-applications/building-blocks/pubsub/pubsub-overview/) — competing consumers and independent subscriptions.
- [Restate: service types](https://docs.restate.dev/foundations/services) — keyed state and per-key serialization.
- [CloudEvents 1.0.2 specification](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md) — source/event identity versus subject identity.
- [Gmail: push notifications](https://developers.google.com/workspace/gmail/api/guides/push) — history retrieval and recovery from missing notifications.
- [Calendar: push notifications](https://developers.google.com/workspace/calendar/api/guides/push) — watch renewal overlap and notification semantics.
- [GitHub: webhook best practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks) — verification, asynchronous handling and redelivery identity.
- [Nylas: handling duplicate webhooks](https://developer.nylas.com/docs/cookbook/use-cases/build/handle-duplicate-webhooks/) — distinct events about one resource and ordering considerations.
- [Trigger.dev: task idempotency](https://trigger.dev/docs/idempotency) — deduplicated triggers do not make task bodies safe to repeat.
- [Workato: infinite loops](https://docs.workato.com/en/recipes/infinite-loops) — cross-automation loops and explicit provenance.
- [Enterprise Integration Patterns: Process Manager](https://www.enterpriseintegrationpatterns.com/patterns/messaging/ProcessManager.html) — durable process coordination and the cost of unnecessary centralization.
- [Where Does Exactly-Once Live?](https://arxiv.org/abs/2609.29095), September 24, 2026 — preprint; simulated ambiguous writes and model/harness/tool-contract effects.
- [Temporary Authority, Permanent Effects](https://arxiv.org/abs/2607.10487), July 11, 2026 — preprint; stale authority at the durable-action boundary.
