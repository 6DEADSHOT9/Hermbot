# Work outcomes and human requests implementation review

Implemented and deployed locally on October 11, 2026 at [localhost:8780](http://localhost:8780). This closes the application change described in [WORK-OUTCOMES.md](WORK-OUTCOMES.md). It does not close Phase 4's remaining real private-write acceptance gate, resume the old Kaggle request, or send an external message.

## Result

Work now keeps required results across turns and delegations. A report can be ready while a requested Slack delivery still needs an answer or review. Finishing a bot execution cannot make that delivery disappear or claim that it was sent. Work cards show required-result progress, reasons and evidence strength; historical completions retain their original record and are labeled accordingly.

Ordinary questions are persistent typed requests. Text questions render an answer field; choices render meaningful options. The owner can answer in the card or in ordinary chat through automatic association. The original asking task receives the actual answer, preserves its work association and resumes once after its existing execution settles. An answer is separate from permission to make an external change. Legacy ordinary `hermbot_review(kind=input)` calls translate to this flow; secure computer input retains its separate takeover and acknowledgement flow.

Connector drafts bind to a required result and its revision. Reviews show the destination, message and other action fields without requiring the owner to edit JSON. The Slack posting contract explicitly accepts a confirmed workspace member ID for a bot-to-person DM, while history reads still require a conversation ID. Exact approvals, account/action/resource restrictions and standing permissions remain enforced. A successful Slack receipt must include `ok`, `channel` and `ts`; acceptance does not mean the person read it and does not require history readback.

## Implementation boundaries

- Additive migration 010 stores outcomes, task assignments, evidence and typed questions, and adds operation bindings. Historical work remains version 0; new supported runtimes use outcome version 1.
- Root preparation records requirements and originating owner messages. Requirements have stable keys, ordered positions, dependencies and individual revisions. Helpers receive only their assigned outcomes and explicitly granted inputs. A helper without a connection does not receive its connection ID or credentials.
- Artifact evidence requires an existing accessible artifact published into the work's conversation. Connector evidence requires a matching successful backend operation. Execution checks and qualitative results are explicitly labeled `model_reported`; these are weaker than provider receipts or artifact existence.
- Completion derives from active required results and unsettled external operations, independently of execution lifecycle. Optional uncertain external effects also prevent a false completed status. Empty contracts do not vacuously complete new work.
- The owner can remove an unneeded requirement with an expected revision, retaining its history. Completed or unsettled external effects cannot be discarded this way. Uncertain native effects require explicit owner evidence; connector uncertainty retains the existing reconciliation service.
- One canonical native-tool policy is loaded by the worker, backend context and supported Hermbot plugin. Connections continue to preserve owner-enabled native tools. Independently signed-in browser or terminal actions remain outside connector review coverage.

All changes are in Hermbot's backend, frontend, managed plugin and private adapter. The application uses Hermes's supported interfaces. The official pinned image, built runtime and all three running runtimes have identical hashes for all 2,169 upstream Python files. No upstream Hermes patch was added.

## Review findings and fixes

| Finding | Correction |
|---|---|
| An answer can arrive before the questioning execution ends. | Preserve `waiting_input` until settlement, then schedule one continuation with the stored answer. Duplicate identical answers are idempotent; conflicting answers fail. |
| Model routing could invent the answer text. | Ordinary-chat answer routing uses the owner's actual message rather than model-produced answer text. |
| A legacy connector-shaped input might become a question and avoid exact action review. | Check the connector-shaped payload before ordinary-input translation; secure input remains separate. |
| Pending or uncertain delivery could be proposed twice. | Fence unsettled operations per outcome and work, bind payload hashes to outcome revisions, and recheck dependencies and revisions before dispatch. |
| Editing a recipient might leave an old approval or display name usable. | Authenticate and validate the new target before superseding, increment the outcome revision, invalidate the old approval, and remove the old recipient label. |
| Re-preparation could erase backend-resolved target metadata. | Merge omitted target fields with the existing target and require owner provenance for actual changes. |
| Batched dependent results could depend on claim order. | Validate prospective fulfillment as a batch and apply settlement in one savepoint. |
| A failed helper or later completed root could hide required failures. | Persist results across roots and helpers; preserve fulfilled, uncertain and cancelled results, and fail only unfinished assigned requirements. |
| Optional uncertainty or stopping work could imply nothing happened. | Give unknown external effects precedence in the product status and require reconciliation before replacement delivery. |
| Malformed legacy questions or owner-edited connector actions could return a server error. | Validate these inputs and return a sanitized 422 response without changing the original pending draft or requirement. |
| A prefilled draft textarea's accessible name included its message content. | Give each structured field an explicit accessible label, verified through the browser editor. |

Outcome ordering, SQL parameter types, ambiguous SQL columns, answer submission state and UI compatibility issues discovered during verification were also corrected.

## Verification

The initial change passed all 303 cumulative backend tests, Ruff, TypeScript check and production frontend build. The follow-up below brings the cumulative suite to 307 passing tests. Exact test totals are recorded in [backend.xml](../test-results/backend.xml) and [the final audit](../test-results/work-outcomes-audit.json). The added outcome tests cover answers arriving during execution, duplicate/conflicting answers, choices, expiry, cancellation, stale versions, secure-flow compatibility, natural-chat association, partial completion, matching/wrong receipts, missing readback access, dependency cycles, scoped delegation, owner target edits, uncertainty, native reconciliation and preservation of prior results.

## Follow-up: missing result registration and handoff

The owner's next Kaggle attempt exposed an enforcement gap in the new integration. Sage prepared an update with no results and delegated lookup and delivery preparation without outcome IDs. Quill correctly discovered the confirmed member-ID route, but the backend rejected its draft because no connector result was assigned. Both agents then claimed that the preparation schema had no `outcomes` field and asked the owner to repair internal IDs. The installed runtime's tool definitions actually include `hermbot_prepare.outcomes`, `hermbot_delegate.outcome_ids` and `hermbot_finish.outcomes`. The earlier acceptance showed these fields worked when supplied; it did not enforce their use before delegating. Once all turns stopped, an empty contract also incorrectly left the work card in `planning`.

Hermbot now rejects empty owner-facing preparation for outcome-version-1 work, including updates to historical conversations. An earlier ready plan cannot bypass missing registration. Such work cannot create a helper without relevant outcome IDs; a lookup can reference the delivery it supports without claiming it has delivered anything. Helpers still prepare independently and cannot redefine the owner's requirements. Authenticated context and worker guidance explain the supported fields and direct agents to repair preparation or handoff themselves instead of asking the owner for internal IDs. Legacy outcome-version-0 execution remains compatible. An empty contract with no active executions now shows `needs_attention` rather than perpetual planning.

Four added regression cases cover empty preparation, stale ready plans, rejection before helper creation or handoff-budget use, and upgrading a legacy work item before a manager hands a result to a peer with its own Slack access. All 307 tests and Ruff pass. The original aborted Kaggle root and its recorded messages remain unchanged; the application does not resume it or send a Slack message. These fixes are in Hermbot's backend and policy, with no Hermes or managed-runtime source change and no bot recreation.

The [real handoff acceptance](../test-results/outcome-handoff-acceptance.json) ran Sage without Slack access and Quill with its existing connection. Sage registered an artifact and a connector result, handed the artifact result to Quill, and retained the pending connector result across continuation. The initial “for my review” wording produced a draft-review question; the recorded clarification explicitly requested an app action approval rather than another wording question. Sage then handed the connector result to Quill, which created the pending Slack approval for the exact supplied member ID and message. The operation was bound to Sage's result, no internal-ID question was needed, and the work remained `needs_approval`. The synthetic draft was denied without dispatch, its task tree stopped and only its temporary group archived. This verifies the actual handoff/review path while retaining the initial clarification as evidence rather than claiming the first draft was a connector approval.

Real-model acceptance used the owner's configured provider and all three original bots:

1. Scout asked whether an inventory heading should be Office or Home, received Office, published the actual table with apples 2, bananas 3 and total 5, and fulfilled its artifact result.
2. Sage delegated to Quill, received Quill's published table with pencils 4, pens 6 and total 10, inspected its contents and fulfilled both the artifact and review results.
3. Quill prepared an exact Slack DM review using a synthetic member ID directly, without asking for a DM channel. The draft was bound to its required result, denied without dispatch and its test task stopped. No Slack message was sent.

The saved [live acceptance](../test-results/work-outcomes-acceptance.json) records these results. The temporary test group was archived after all its tasks settled; owner groups were preserved. Playwright checked text answers, choices, partial progress, readable connector previews, structured recipient/message edits preserving the connection and outcome binding, JavaScript errors and overflow at desktop 1,440 px and mobile 390 px. The native-tools UI regression also passed. Evidence is in [work-outcomes-ui.json](../test-results/work-outcomes-ui.json), [the desktop screenshot](../test-results/work-outcomes-1440.png) and [the mobile screenshot](../test-results/work-outcomes-390.png).

The final audit preserved all three owner groups, every recorded connection and workspace setting, bot models/tools/skills/resources and all nine private volumes. Each bot revision increased once to apply the managed plugin upgrade. All three runtimes remain compatible and healthy, with read-only source, a protected managed plugin, a non-root gateway, zero effective Linux capabilities, no Docker socket and no privilege escalation. [work-outcomes-audit.json](../test-results/work-outcomes-audit.json) records the checks; `scripts/work_outcomes_audit.py` can repeat them without writes.

## Practical limits

The model still interprets which results the owner requested. The backend checks structure, provenance, permissions and evidence; it cannot prove that every sentence was interpreted correctly or that a research answer is accurate. Native execution checks remain model-reported unless stronger application evidence exists. No universal directory resolver or new provider lookup scope was added: use existing authorized discovery or ask a typed question when the target is ambiguous. A real Slack delivery still needs a fresh exact approval or applicable standing permission. Legacy cancelled drafts and historical work were not restarted or fabricated into verified results.
