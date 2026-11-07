# Phase 5.4 — Attention and notifications: researched implementation plan

Researched October 26, 2026; implementation added the same day. Sections 1–18 preserve the approved design and its acceptance contract. **Implementation, migration, controlled verification and native owner-answer acceptance are now complete; actual desktop Web Push delivery and final fixture cleanup remain pending.** See section 19 and [the implementation review](PHASE5-ATTENTION-REVIEW.md). Phase 5.3 remains complete; the overall Phase 5 exit is a separate pending gate.

## 1. Outcome and fixed boundaries

The owner can leave the browser and return to a reliable explanation of what needs an answer, what requires a decision, what finished and what requires recovery. Browser notifications can bring the owner back, subject to explicit permission, preferences, quiet hours, device support and local availability. Reading a notification never authorizes work.

Use Hermbot's existing PostgreSQL state, authenticated owner APIs, work/result acceptance, question/review handlers, execution and native Hermes cron wake-up. Keep upstream Hermes source and owner-enabled native tools unchanged. Add no LLM polling, model-generated notification routing, forced checkpoints/yields or replacement orchestration system.

The scope includes the attention inbox, unread results, real questions/reviews, failures/uncertainty and actionable operational blockers, browser push, category preferences, quiet hours, delivery deduplication and useful manager summaries with source links. It covers ordinary and managed work from manual, scheduled and event-triggered admission. Attention grouping must not change work ownership or merge independently admitted requests.

Preserve the owner-approved single-machine/single-owner scope. Public hosting/HTTPS rollout, collaboration membership, mobile device control, VM migration, extra connector providers, email/SMS/Slack notification delivery and the broad evaluation suite are not part of 5.4. The connected-service coordination project still starts only after all Phase 5 and its exit gate. Mobile use remains Phase 6A after Phase 6 without renumbering Phase 7.

## 2. Code inspected and what already exists

Source inspection for this plan is read-only. Earlier recorded tests are evidence for earlier builds; they were not rerun during this research.

| Current surface | Reuse | Missing integration |
|---|---|---|
| `backend/db.py:event`, `message`; `events` and `messages` | Transactional application facts and idempotent message identities | No durable owner attention/read state or push delivery queue. Raw tool/stream events are too noisy to be notifications. |
| `backend/app.py:state`, `events`; `web/src/App.tsx` | Cookie-authenticated state, SSE updates, reconnect refresh and five-second state polling | No inbox or cross-session unread state. SSE supplies invalidation, not durable delivery acknowledgement. `/state` caps old tasks; attention cannot depend on that recent-task slice. |
| `work_items.py:list_items`, `outcomes.py:projection`, `result_acceptance.py` | Required current results, accountable owner, contributions and truthful overall state | Extract/reuse a bounded shared status reader; do not implement a second completion algorithm or call the entire workspace projection for every notification. |
| `inputs.py`, `service.py:reconcile_requests`, existing review handlers | Typed questions, exact hashes, expiry, closure, stale-decision refusal and authenticated answers | Share the actionable-state predicates with attention. A historical request row or copied payload is not proof a button remains valid. |
| `connections.py`, connector operations, `knowledge.py`, `lifecycle.py` | Actual decision/effect receipts, generic/connector/runtime/skill/memory/lifecycle/secure-input reviews | Cover all real review kinds, retained uncertainty and source links without copying credentials or secure-input contents. |
| `routines.py`, `triggers.py`, `recurring_management.py` | Definition/run/work/thread identity, shared budgets and selected authority | Produce one useful work-level update and an actionable automatic-pause/source-access warning; avoid one alert per contributor, tick or retry. |
| `activity.py`, `execution_monitor.py` | Observed contact/progress, actual execution assessments | A heartbeat or elapsed-time guess must not generate a new failure or declare a run stuck. Reuse actual recovery states. |
| `auth.py`, `/api/v1/session` | One-day opaque browser sessions, explicit logout, owner bootstrap token | Device push registration/revocation is separate from the short-lived browsing session. Add explicit current-browser logout handling and safe re-enablement. |
| `library.py:public_address`, `connector_http.py:request`, `secrets.py` | Validated/pinned public HTTP transport and encrypted private storage | A client-supplied push endpoint needs the same network boundary; a library's default HTTP client must not bypass it. |
| `worker.py`, `trigger_worker.py`, `compose.yaml` | Existing same-image workers, nonblocking claims, leases, graceful shutdown and collector heartbeat | Push must not occupy a bot slot, delay agent execution or block a provider collector. |
| `web/src/App.tsx`, `Management.tsx`, `InputCard.tsx`, `WorkItems.tsx`, `api.ts` | Current sidebar/owner menu, Radix panels, actual work/review surfaces and React Query | Add one attention entry point, preferences under the owner menu, safe cross-conversation/thread navigation and permission-aware browser setup. |
| `web/package.json`, static serving in `backend/app.py` | Existing Vite/React build; standard browser APIs | No service worker, PushManager, manifest or notification library is presently integrated. |

Two integration hazards are visible already. App navigation resets thread state when its conversation changes, so a deep link must coordinate loading the conversation and then its thread. Existing SSE uses increasing database event IDs; a new durable projector must not assume those IDs are commit order or that a browser replay is a lossless queue.

## 3. Research findings and comparable implementations

Primary documentation and original project reports were reviewed October 26, 2026. The application choices in subsequent sections are our design conclusions, not claims that these products implement Hermbot's exact policy. No hosted notification platform is selected.

| Comparable system / primary reference | Implementation pattern observed | Application to Hermbot |
|---|---|---|
| [Knock in-app feeds](https://docs.knock.app/integrations/in-app/feeds) | Separates unseen, seen and read engagement; supports links/actions | Distinguish reading from resolving the underlying work. Opening the inbox must not clear every pending decision. We do not need a separate seen-analytics feature in 5.4. |
| [Knock send windows](https://docs.knock.app/designing-workflows/send-windows), [timezone rules](https://docs.knock.app/manage-your-account/account-timezone) | Delivery can wait for recipient-local windows while workflows continue | Quiet hours control interruption, not bot execution or immediate in-app visibility. Resolve an explicit IANA timezone and test DST. |
| [Linear Inbox](https://linear.app/docs/inbox) | Attention-oriented navigation, keyboard actions, priority/other views and snooze | Use a small set of useful views with source navigation. Individual snooze/reminder scheduling is not necessary to deliver the current quiet-hours requirement. |
| [GitHub notification API](https://docs.github.com/en/rest/activity/notifications) | Thread identity and unread state; bulk read can use a cutoff | Read operations need the version actually presented to the user. A concurrent update must not be marked read accidentally. |
| [Prometheus Alertmanager](https://prometheus.io/docs/alerting/latest/alertmanager/), [configuration](https://prometheus.io/docs/alerting/latest/configuration/) | Groups related alerts, inhibits dependent alerts and silences interruption | Group by real work/source identity and suppress known duplicate symptoms. Never infer unrelated causation merely because two failures happened together. |
| [Slack notification pauses](https://slack.com/help/articles/214908388-Pause-your-Slack-notifications) | User-controlled temporary/scheduled interruption policy | Make the pause scope and next allowed notification time visible. Do not silently extend an expiring question because push was paused. |
| [AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html) | Atomically saves state and a durable delivery obligation; consumers handle repeats | Commit attention invalidation with application state. Produce queued push obligations transactionally with attention changes. Never send push inside a database transaction. |
| [PostgreSQL row claims](https://www.postgresql.org/docs/current/sql-select.html), [sequences](https://www.postgresql.org/docs/current/functions-sequence.html) | Nonblocking queue claims; sequence allocation is distinct from transaction completion | Use explicit pending records/claims, not a `max(events.id)` progress watermark. Test a lower ID committing after a higher ID. No Kafka/Redis requirement. |
| [Web Push protocol, RFC 8030](https://www.rfc-editor.org/rfc/rfc8030) | TTL, urgency, topic replacement, subscription expiry; accepted does not mean delivered | Bound freshness, replace obsolete queued pushes where supported, remove only failed endpoints and report delivery stages honestly. |
| [Encryption, RFC 8291](https://www.rfc-editor.org/rfc/rfc8291), [VAPID, RFC 8292](https://www.rfc-editor.org/rfc/rfc8292) | Standard payload encryption and application-server identification | Use maintained implementations and stable private server keys. Do not handwrite cryptography or treat VAPID as owner/session authentication. |
| [MDN Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API), [notification options](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerRegistration/showNotification), [click handling](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerGlobalScope/notificationclick_event) | Service-worker delivery, subscription capability endpoints, notification tags and client navigation | Keep endpoints private, use one notification per logical subject/tag where possible and fetch actual state after authenticated navigation. |
| [WebKit Meet Web Push](https://webkit.org/blog/12945/meet-web-push/), [iOS/iPadOS support](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/) | User-visible push requirement; Apple integration can work with Safari closed; iOS Home Screen/user gesture requirements | Do not send invisible refresh pushes or depend on a page/SSE being open. Use feature detection and actual-device evidence. |
| [MDN secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts), [Chrome push FAQ](https://web.dev/articles/push-notifications-faq) | Localhost can be trustworthy; background behavior differs across browsers/OS | Local desktop acceptance is viable without public inbound hosting. Plain HTTP LAN access is not equivalent; browser/OS availability must be tested, not assumed. |
| [pywebpush source](https://github.com/web-push-libs/pywebpush), [public request-session boundary](https://raw.githubusercontent.com/web-push-libs/pywebpush/main/pywebpush/__init__.py), [published package guidance](https://pypi.org/project/pywebpush/) | Encryption/VAPID with a supplied `requests_session`; default TTL is zero; documentation says a supplied VAPID claims dictionary is mutated | Candidate Python dependency: pin a tested stable release and inject the existing constrained transport. Set TTL explicitly, create fresh per-endpoint claims and sanitize exceptions. Its single-maintainer disclosure is a dependency-maintenance consideration, not a reason to handwrite cryptography. |
| [OWASP SSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html) | Treat caller-selected destinations and redirects/DNS resolution as a trust boundary | Validate and pin the actual outbound connection on each send; reject internal/loopback destinations and redirects. |

### Recent original reports and the concrete tests they motivate

These are upstream reports, not reproductions in Hermbot. A closed issue or merged branch does not establish a particular published package version contains its fix.

1. [Novu #10762](https://github.com/novuhq/novu/issues/10762), April 17, 2026: duplicate socket/cache inserts produced repeated rows with multiple clients. [PR #10785](https://github.com/novuhq/novu/pull/10785) merged into `next` June 23, 2026 and filters existing IDs on insert. **Test:** two tabs, repeated SSE updates and refetch all converge to one row with one server count; dedupe the client as well as the queue.
2. [Novu #10817](https://github.com/novuhq/novu/issues/10817), April 21, 2026, open when reviewed: unread/filter cache drift after read operations. **Test:** mark read in one tab while another filter/page is open; counts and filtered membership must update together from the server.
3. [Novu #12373](https://github.com/novuhq/novu/issues/12373), August 18, 2026, closed when reviewed: reported digest self-merging after reexecution and losing the batch. **Test:** repeat a quiet-hours summary/recover its worker without merging a delivery into itself, dropping its members or sending an unbounded replay.
4. [Novu #12532](https://github.com/novuhq/novu/issues/12532), September 2, 2026, open when reviewed: inconsistent invalid-device-token cleanup was reported for FCM errors. It concerns a different delivery API from standards Web Push. **Test:** classify actual Web Push status codes centrally and retire only the affected subscription; keep the owner's other browsers usable.
5. [Novu #12650](https://github.com/novuhq/novu/issues/12650), September 14, 2026, open when reviewed: identity/index disagreement after subscriber recreation reportedly left digest work stuck. **Test:** registration replacement, VAPID rotation and queue uniqueness use the same subscription identity/generation; old pending rows cannot block a fresh valid registration. No multi-user subscriber platform is introduced.

The broader recurring lessons are separate state machines, transactional delivery obligations, idempotent materialization, level-based reconciliation, bounded queues, explicit identities and current-state validation at trust boundaries. These are ordinary software-system patterns; an LLM is not needed to decide whether a stored question remains pending.

## 4. Owner experience and defaults

Add **Attention** beside the existing Routines navigation, using the same compact sidebar layout rather than a fourth always-expanded teammate section. Provide one badge for unread attention updates and an independently labeled count of outstanding items needing the owner. On narrow screens use a full-width accessible panel/page. Notification settings live in the existing owner-name menu; Connections and File library stay there.

The inbox has **Needs you**, **Updates** and a quiet **History** filter. Needs you contains live answers/reviews, recovery/reconciliation and actionable setup/budget blockers regardless of read status. Updates contains current unread results and useful state changes. History preserves resolved/read/dismissed items. Show a concise reason, teammate/accountable manager, work/routine/source, time, current status and an Open work/review/result action. Do not surface internal schema IDs in normal copy.

Opening the inbox alone marks nothing read. Opening a notification's actual target and rendering the referenced content can acknowledge exactly the observed version; explicit Mark read/unread is also available. A failed navigation/401 does not mark it read. A **Mark visible read** action submits only the displayed IDs and versions (at most 100). It does not clear unseen pages or future updates. An unbounded Mark all read operation is not needed for this milestone. Active owner decisions remain in Needs you after being read.

Dismiss/clear applies to informational or resolved inbox entries. It does not delete a task, cancel a request, answer a question or approve an operation. To remove a live question or change work, open its existing owner control. Restoring work or receiving a material new update can legitimately produce unread attention again; replaying the same fact cannot.

Defaults: durable in-app attention is on; browser push is **off until explicit opt-in**; all four push categories are initially selected when enabled: Questions, Reviews, Results, Problems/setup. Quiet hours are off initially, with an editable 22:00–08:00 preset. Quiet time uses the owner's explicit preference timezone, initialized from workspace timezone; browser travel does not silently alter it. In 5.4 browser notification text is always generic: “Hermbot has an update — open Hermbot to view the current status.” Detailed permitted content stays in the authenticated app. This avoids a private-preview mode and stale copied payloads.

## 5. Notification eligibility and semantic identities

| Source condition | Attention representation | Push behavior / closure |
|---|---|---|
| Valid pending ordinary question | One entry keyed to exact input request ID and current question/work revision | Eligible immediately under preferences; close on answer, removal, expiry, supersession, work stop or invalid current authority. |
| Valid pending review, including connector/runtime/lifecycle/skill/memory/secure input | One entry keyed to exact approval ID and payload/attempt revision | Open the existing exact review. Edit/replacement closes the old entry and links the new review; no OS-level Allow/Reject buttons. |
| Accepted final work results and settled relevant execution/effects | One work-level result entry; meaningful accepted-result fingerprint/version | Short 15-second result coalescing window. Subsequent accepted result revisions update the entry and unread version. An artifact alone or native `completed` alone is insufficient. |
| Genuine required-work failure, blocked requirement needing the owner, uncertainty/unresolved effect, exhausted budget | One problem entry per actual work/blocker episode | Uncertainty is immediately eligible. Settled failure/setup warnings get a 30-second anti-flap window; current state is checked again. Repeated polls do not create new episodes. |
| Automatic routine/trigger pause or source/connection access/resync failure requiring setup | One entry keyed to the actual definition/source/connection and reason episode | Link to the exact settings/history. Group dependent symptoms only when stored references prove the common cause. Clear on repair; user-paused/archived items are not errors. |
| Manager contributor activity, automatic retry/recovery, ordinary genuine dependency wait | Update the work summary and links | No separate push for each internal transition. A manager repairing a helper failure should not flood the owner. |
| Owner status/discussion/thanks and answer-acknowledgement execution | Ordinary chat | Do not invent a work item or “work completed” alert. Existing work-intake semantics remain authoritative. |
| Tool starts/deltas, heartbeat/contact, admission accepted, scheduler tick/skipped overlap, connector queued/dispatching | Execution/history detail | No result or failure notification merely because these events occurred. |

Legacy work without accepted-outcome semantics may produce an accurately labeled **reply available** update for an actual owner-facing assignment, not a claim of verified completion. The normal task result remains inspectable. Required manual action receipts and unknown effects remain distinct.

The stable identity includes recipient, subject type/ID and notification purpose. Each scheduled/event occurrence is a different work subject; do not dedupe all runs under a routine name or all tasks in a Slack channel. Use the static managed-occurrence association where present, not a cosmetic message/work classification that the owner can change. Within a work, sources retain exact question/approval/operation references so independent decisions never overwrite each other. The unique index, lookup, API mutation and delivery key must use the same identity.

## 6. State model and unread correctness

Keep three independent facts:

1. **Source state:** the existing work/question/review/operation actually needs attention, is resolved, expired, superseded, stopped or uncertain. Existing domain handlers remain authoritative.
2. **Owner attention state:** the last material version the owner read/dismissed. This is shared across their browser sessions, not localStorage state.
3. **Transport state:** a browser delivery is pending, deferred, dispatching, accepted by its push service, failed, expired or suppressed. None means the underlying work is approved or the owner read it.

An entry has a stable ID and a material version. Changes to required results, question identity, actionable reason or meaningful status change the version/fingerprint. Repeated invalidations, heartbeat timestamps and unchanged error polls do not. `read_version` cannot advance past the version the owner actually saw. If version 8 arrives while a request marks version 7 read, version 8 stays unread. Marking an existing version unread again changes attention state only; it does not enqueue another push or create a new work episode. Reopening a genuinely resolved problem is a new material episode; old acceptance/decision history is retained.

Use the same source-state predicate for detail, counts, list filters and eligibility. Derive expired-prompt ineligibility from current server time even before an expiry sweep runs. The projector changes only attention/delivery records: it does not answer, approve, cancel or reschedule domain work. Existing domain reconciliation remains responsible for actual request/task transitions. Existing permission/expiry checks still run at the actual answer/review endpoint. Invalid current authority shows a specific blocker/link; an old card never becomes an authorization token.

History entries retain safe references, not a private transcript copy. Archives/removal hide obsolete actions and suppress queued push while leaving a useful “no longer active” detail. Restoring a conversation does not replay old pushes or reactivate its routines automatically.

## 7. Smallest durable backend design

Use PostgreSQL and the application image, with five focused record groups rather than a general notification workflow platform:

| Proposed record | Minimum role / important constraints |
|---|---|
| `attention_items` | Recipient `owner`, canonical subject/purpose, stable ID, safe target references, material fingerprint/version, source/action state, read/dismissed version and timestamps. Unique semantic key; indexes for recipient/status/update paging. No copied answer, exact action payload, raw provider event or credential. |
| `attention_pending` | Transactional dirty-subject obligations keyed by subject, with coalesced generation/availability and failure metadata. A repeat marks the subject dirty; processing an old generation cannot erase a newer update. |
| `notification_preferences` | Existing owner identity, revision, per-category push toggles, enabled/disabled quiet interval and explicit IANA timezone. Durable in-app action visibility cannot be disabled by a push preference. |
| `push_subscriptions` | Server-issued browser-installation ID, recipient, encrypted endpoint/keys, endpoint hash uniqueness, configured-origin/key generation, device label, enabled/revoked state and safe timestamps/error code. No bootstrap token and no browser fingerprinting. |
| `notification_deliveries` | Unique delivery purpose/version/subscription generation, queued/deferred/claimed/accepted/etc. state, due time/freshness deadline, lease/fence/attempt count, safe result code and optional quiet-summary coverage reference. No plaintext endpoint or signed Authorization header. |

Migration must be additive and idempotent, preserve schema/history and add only indexes used by real queries. Reuse existing encryption and authenticated owner identity. Do not create Phase 8 users/tenants/memberships. Historical reconstruction, initial seeding and record retention are addressed below, not left to an unbounded event replay.

### State-to-inbox path

Relevant semantic domain transitions enqueue or increment a dirty subject **in the same database transaction** as their existing event/state change. Extend the shared `db.event` path for a small explicit allowlist and supply canonical references where an event currently lacks them. Trace real direct-SQL transitions and lifecycle/expiry paths; an event hook alone does not prove coverage. Ignore native stream/tool noise. Duplicate event insertion must not create another semantic notification.

A background projector claims pending subjects, reads the current canonical state through shared bounded domain readers, updates attention versions and queues eligible push obligations in one short transaction. It does not render from arbitrary event payloads. The new read path must share domain eligibility with existing UI/actions rather than reproduce a simplified state machine.

Use current-state reconciliation in addition to transition hints: seed current actionable questions/reviews/blockers at migration, schedule expiry reevaluation, and periodically inspect bounded pages of live sources and existing active attention. Run a bounded catch-up for recently completed work. This repairs omitted hints, time-based changes and recovery without repeatedly invoking a model. Bootstrap must not push years of old results: old completed history is initially read; existing live decisions appear in the inbox but do not trigger a retroactive push burst when the feature is first enabled.

Do not store a single `max(events.id)` processed cursor as the sole correctness boundary. Sequence allocation is not commit order: transaction A may allocate 100 and commit after B commits 101. A pending row written in each transaction remains claimable after either commit. SSE remains an acceleration mechanism; reconnect/focus and periodic fetch recover durable inbox/count state.

### Worker isolation and claims

Add a small `notification-worker` process using the existing app image. It projects attention, handles expiry/catch-up and sends push; it uses no model keys or bot runtime networks and consumes no agent slots. Keep its database work short and bounded. Prefer the existing lease/generation patterns and `FOR UPDATE SKIP LOCKED`; no Redis, Kafka, Temporal, Novu or Knock deployment.

Follow domain-lock-before-attention-before-delivery order; dirty claims must not hold an attention-row lock and then block on a domain lock. Release/fence a claim before reading domain state or use nonblocking ordered acquisition, then conditionally apply the captured generation. The projector must not enqueue itself through attention-change events. Network sends occur only after transaction commit, outside all domain locks. Claim rechecks current permission/policy/source relevance/read state, then releases locks before HTTPS. Record the result only for the same lease/subscription generation.

## 8. Browser push transport and truthful delivery

Use standards Web Push, a small service worker and `pywebpush` as the candidate backend encryption/VAPID dependency. At implementation, inspect and pin a compatible **stable release and its dependency hashes**, using its public request-session interface with the existing pinned/bounded HTTP transport. The inspected upstream main branch demonstrates that interface; it is not a license to depend on unreleased internals. Do not handwrite encryption or call private request-builder methods. Validate key lengths and bounded payload sizes at registration even if the library also validates them.

Generate a stable VAPID keypair through the private setup path; never regenerate it on a worker restart. Keep the private key only in private server configuration and the public key in the owner setup API. Use a valid configurable contact identity; do not place localhost in a VAPID contact URL as an assumed cross-browser solution. Create fresh VAPID claims for every endpoint: the candidate library can mutate its input dictionary, so reusing one across two vendors can retain the wrong audience. A key rotation changes subscription generation and requires explicit re-registration; old queued work cannot silently switch to unrelated credentials.

The actual flow is: owner clicks Enable browser notifications → feature/origin check → browser permission request in that user gesture → service-worker registration → `PushManager.subscribe({userVisibleOnly:true,...})` → authenticated private server registration → optional explicitly requested test push. Existing granted permission can be reconciled without prompting; disabled/revoked registration cannot silently reenable itself on page load. Denied permission shows browser-settings guidance, not a repeated prompt loop. Unsupported/insecure environments keep full inbox access.

Use an explicit TTL: initially at most one hour for a single live actionable prompt and four hours for informational/results wakeups, further bounded by source expiry, delivery freshness and remaining validity. Quiet-hour deferral happens before transport; do not let a vendor queue decide when quiet time ends. Do not replay push obligations older than 24 hours after downtime; their inbox entries remain available. These are initial product constants, not user-facing timing controls. Apply actual provider limits and size constraints when implementing.

Use a stable opaque RFC Topic hash (maximum 32 permitted characters) and browser notification tag per attention purpose, with `renotify:false` for replacement/retry. Preserve independent requests as separate inbox entries. Topic/tag replacement reduces repeated interruption; it does **not** establish exactly-once display across all OS/browser failures. HTTP success means accepted by the push service, never delivered/read. A timeout after possible send can be retried within bounded policy using the same logical identity, with that duplicate-display limit explicit.

Suggested initial retry policy: one claim at a time per delivery/subscription; 15-second request deadline inside a 60-second lease; exponential backoff with jitter and at most five attempts within freshness. Honor valid `Retry-After` values for 429/503 without extending freshness. Retire the affected endpoint on authoritative 404/410. Do not delete all device subscriptions on a 401/403 VAPID/configuration failure; report a safe setup warning and stop futile retries. Treat malformed subscriptions/oversize payloads as permanent failures. Keep a dead/error state inspectable; a bad endpoint cannot starve healthy ones. Configuration repair may retry unsent fresh wakeups explicitly, not replay already accepted notifications.

## 9. Quiet hours, batching and interruption policy

Quiet hours apply to all push categories initially, with **no hidden urgency bypass**. Questions/approvals remain immediately visible in-app and retain their real expiry. If the owner changes preferences, reevaluate pending unsent deliveries; a preference save does not restart work, reset read state or create a backlog notification storm.

Use a daily local interval `[start,end)` with cross-midnight support. Reject equal start/end to avoid accidentally meaning either zero or 24 hours; disabling push handles a fully muted day. Compute next eligibility with `zoneinfo`/IANA rules, store absolute due timestamps and recheck the active rule immediately before claim. On spring-forward use the first actual allowed instant; on fall-back do not send twice for the repeated local end. Changing timezone or hours recomputes pending due times. Keep occurrence schedules/timezones independent.

While quiet, keep inbox entries current and coalesce wakeups into a **generic catch-up summary per browser and quiet-period end**, using a stable owner/preference-policy/period/subscription-generation key. Covered individual wakeups stay recorded as suppressed/covered; the summary never merges with itself. At release, query the currently relevant, unread covered attention. Drop answered/expired/stopped/read/superseded entries and skip an empty summary. If one relevant item remains it can link to that item; otherwise it opens Attention. A changed rule must supersede the old pending summary rather than enqueue both under two policy revisions. A summary already accepted is not resent merely because its projector retries.

Outside quiet hours, collapse rapid updates of one logical item and apply a simple per-browser burst cap of three push wakeups per minute; remaining fresh updates become a generic catch-up wakeup at the next eligible minute. This throttles interruption, not inbox visibility or source work. Recheck relevance at release. Do not implement per-notification snooze, daily newsletters, escalation chains or an arbitrary workflow designer in this milestone.

## 10. Service-worker behavior and platform constraints

The service worker receives a **generic visible notification**, an opaque attention/delivery reference and a same-origin destination. It must show the notification from `event.waitUntil(...)` even if the app is open, cookies expired, the backend is temporarily unreachable or detailed fetch fails. Do not make visible delivery depend on an authenticated background fetch. In particular, do not silently consume received pushes to suppress a stale message or update a badge: Safari's user-visible rule makes that unsafe. Relevance suppression happens on the server before sending; a residual in-flight race is handled safely on click.

Generic payloads make out-of-order delivery harmless: an older alert never contains an actionable old draft or overwrites a detailed newer decision. On click, focus an existing same-origin client or open a new one at a validated `/?attention=<opaque-id>` destination. After normal authentication, fetch the current subject and navigate to its conversation/thread/work/review, or show that it is resolved/expired/removed. Preserve the intended destination across login without accepting arbitrary return URLs. Closing an OS notification does not mark the work read, answered or approved.

Serve a small same-origin service worker and, where needed for installable-app capability, a minimal manifest/icons through the existing build. Feature detection decides setup. Do not add offline caching of owner APIs, transcripts, artifacts, sessions or provider data; no general offline/PWA project is required. SW updates must retain compatible message handling and subscriptions, clean only Hermbot-owned obsolete assets and not unregister another app. In-app read/reconciliation can close matching displayed notifications on a connected browser where supported; do not send invisible revocation pushes or promise remote removal from every OS tray.

Browser/OS behavior must be recorded accurately. WebKit documents Safari delivery even when Safari is closed on supported macOS, whereas browser/platform background behavior elsewhere varies. Do not generalize an old Chrome FAQ into a universal “browser must remain running” rule. iOS/iPadOS support requires a suitable Home Screen web app and user gesture on supported versions. Actual features/versions must be checked during implementation.

The current `http://localhost:8780` desktop origin can be trustworthy for these APIs. Public inbound hosting is not required for standard outbound Web Push. The notification worker and browser still need access to their push services, and Hermbot's computer/Docker must be awake to produce/send new updates. A phone opening `http://<LAN-IP>:8780` does not get the localhost secure-origin exception; HTTPS reachable by that phone is a separate deployment prerequisite. No tunnel, domain or public endpoint is implicitly authorized by this milestone. Mobile viewport tests are in scope; mobile-device push acceptance cannot be claimed without that prerequisite and real device evidence.

## 11. Privacy, authorization and subscription lifecycle

Only authenticated owner requests can register/list/revoke their subscriptions or change attention/preferences. Generic opaque IDs and browser endpoints confer no task/action authority. Models, connected provider payloads and native tools cannot select a notification recipient, supply a push endpoint or override privacy/quiet policy.

Encrypt subscription endpoints and key material using the existing private-storage mechanism; store a hash for uniqueness, not plaintext copies in receipts/logs. Send with HTTPS only, no userinfo/fragment/custom port, bounded lengths, validated public DNS addresses, pinned socket and original-hostname TLS verification; disallow redirects/proxy inheritance. Registration and each actual send are validation boundaries. An otherwise valid owner-supplied endpoint must not turn a worker into an internal-network HTTP client. Sanitize library exceptions and never enable verbose/curl output in normal operation.

Always use generic OS-preview text in 5.4. In-app summaries can show authenticated work titles/status and source links, but never duplicate secure input, secrets, raw provider payloads, full private bot transcripts or exact reviewed arguments into notification storage. Treat untrusted names/errors as plain text; no arbitrary HTML or auto-fetched external preview images. Logs contain IDs/state/reason codes, not the delivery URL, authorization header or answer contents.

Register a browser installation with a server-issued random HttpOnly/SameSite device cookie/handle that is an association, not an access credential. Give the association and registration the same 90-day maximum lifetime, retain an explicit server expiry and stop delivery when it expires; show when re-enablement is needed. This prevents a live registration outlasting the browser association required for logout cleanup. Subscription identity is endpoint hash + recipient/configured origin; every replacement uses an incremented generation. Normal one-day login expiry does not unexpectedly disable generic push; private content still requires fresh login. Explain this when enabling notifications. **Explicit logout disables this browser's server registration, attempts PushManager unsubscribe and clears local notification handles/displayed notifications.** Other browsers are unchanged. Server revoke succeeds independently of whether browser unsubscribe succeeds. Re-enabling requires an authenticated explicit owner action.

Settings list safe device labels, last confirmed activity and enabled/error state, with Disable this browser and Revoke other device controls. Handle OS permission revoked outside the app, expired vendor subscriptions, cleared site data, browser reinstall, origin changes and VAPID rotation without automatically recreating a disabled subscription. Current state is reconciled on login/focus; an unavailable `pushsubscriptionchange` event is not the only recovery mechanism. Already accepted in-flight notifications may still arrive after disable/logout; they stay generic and cannot bypass login.

## 12. Manager summaries and navigation

One attention summary represents one accountable work/occurrence: manager, accepted/required result counts, current required owner decisions, blocked/uncertain reasons and links to actual results, contributing assignments and provider receipts. Reuse the existing manager report and backend state. A deterministic summary can say “2 of 3 required results ready; one review required” without another model call. Do not replace substantive manager reports or pretend a helper's self-reported success fulfills a shared result.

If an owner must decide two distinct reviews, retain two exact decision entries even when a work summary groups their presentation. Progress-only summaries do not generate repeated push. A known connection/access blocker can group affected work links under its actual source, but independent errors remain visible. A “no progress” heuristic from contact timestamps alone does not become a new failure notification.

The deep-link resolver must load the canonical source by ID, not search only `/state.tasks` or a single 100-message chat page. Resolve archived/removed/out-of-range targets honestly. Coordinate existing `setActive` and thread-loading behavior so a conversation-change effect does not erase the requested thread. Preserve history/back navigation and restore focus after opening/closing panels. A link opens existing work/review controls; it does not submit an action from a service worker or GET request.

## 13. Proposed API and frontend contract

Exact route names may follow current naming during implementation; the semantics below are required.

| Operation | Required behavior |
|---|---|
| `GET /api/v1/attention` | Filtered, bounded cursor page; item IDs/material versions/source states; counts from the same canonical eligibility/recipient scope and read snapshot; stable ordering and safe references. Use one statement/snapshot or a short read-only repeatable-read transaction, rather than separate inconsistent count/list reads. No raw source payload. |
| `GET /api/v1/attention/{id}` | Fresh target/source-state resolution, including resolved/expired/archived state and permitted destination. Works beyond `/state` recent-task limits. |
| `POST /api/v1/attention/read` | Explicit item ID + observed version set, idempotent, cannot acknowledge a newer version. Return fresh counts/membership hints. |
| Bulk read | Reuse the read endpoint for the explicit visible ID/version set, maximum 100. New entries, unseen pages and newer versions stay unread; no additional snapshot-token table or unbounded global read endpoint. |
| Read/unread/dismiss mutation | Owner attention state only; no work/decision changes. Validate current item and revision and preserve audit/source history. |
| Preference GET/PUT | Validate revision, categories, IANA timezone and quiet interval. Return next allowed push time and setup status; reevaluate pending unsent deliveries. |
| Subscription register/list/revoke | Current owner only; bounded browser count (initially 10); endpoint/key validation and encrypted storage; idempotent replacement, device association/generation and safe status. Never return decrypted endpoints/keys in device lists. |
| Explicit test-push request | Owner-triggered, rate-limited, same actual delivery path and privacy/quiet rules; ordinary bootstrap/preferences changes do not send a test automatically. |

React Query stores server rows by ID/version and invalidates **all** affected count/filter/page keys after a mutation. SSE `attention.changed` carries only safe invalidation IDs; repeated messages/refetch must not append duplicate rows. Include periodic focused attention refetch and reconnect/focus refresh even if other SSE events are missed. Do not infer global unread count from the current page or decrement badges optimistically without authoritative reconciliation.

Use bounded pagination (default 30/max 100), timestamp-plus-ID ties and explicit paging/filter refresh on changed sort keys. Lists can dedupe changed rows on a continued page; refresh starts from the top to avoid skipped updates. The bulk button is labeled Mark visible read and acknowledges only the submitted page/version set. Counts distinguish “unread updates” from “requires you”; loading/errors/empty/permission-denied states are first-class.

Apply the existing design-taste app-screen skill when implementation begins. Use current type scale, buttons, focus handling and panel primitives. Check keyboard operation, screen-reader names/status announcements without speaking every event, narrow layouts, zoom, long names, dark theme and horizontal overflow. Retain context-menu/sidebar preferences from the earlier UI work.

## 14. Retention, recovery and observability

Use initial bounded retention: keep unresolved/actionable attention until resolved; retain read/resolved history and sanitized terminal delivery records for 90 days. Prune terminal delivery details in small batches without deleting tasks, source events, artifacts, approvals or provider receipts. Retain a minimal tombstone for pruned handled subjects (semantic identity, last fingerprint/material/read version and closure time) while their canonical source exists, hiding it from normal history. Reconciliation must not recreate that subject as unread unless actual new material work occurs. There must be a test for prune → reconcile → no notification resurrection. Do not cap active attention by silently dropping the oldest pending question.

Keep at most 10 browser subscriptions per owner and at most 1,000 fresh pending delivery wakeups per browser. Coalesce excess interruption into a generic catch-up purpose rather than discarding inbox entries. Results/operational history beyond the push freshness window stay in the inbox without mass catch-up push. Queue scans are indexed, bounded and fair; one broken endpoint/subject must rotate behind usable work after a bounded failure delay.

Track oldest pending age, projector/delivery pass heartbeat, counts by safe outcome/reason, expired lease recovery and permanent setup failures. Persist a bounded worker heartbeat/status alongside the owner notification settings without incrementing the policy revision or emitting a new notification for each heartbeat; a container-local file alone is not readable by the API container. Expose delivery-worker unavailable/setup-needed status in settings and the normal health/read path; an inbox cannot be the only place reporting that its own projector is dead. Metrics mean committed attention, push-service accepted or known rejection; do not invent read/delivered telemetry. Prevent notification infrastructure errors from recursively generating notification infrastructure alerts.

Healthy local target: committed relevant state appears in the inbox within five seconds, and an immediately eligible push is claimed within five seconds after projection. Deliberate result/failure coalescing, quiet time, burst throttling and provider/OS delivery are excluded and must show their actual reason. Verify these timings at the agreed local load (three initial bots, bounded sources and multiple browser tabs); they are acceptance targets, not an Internet/OS display-time guarantee.

Recovery guarantees: committed domain changes retain dirty obligations; projector retries materialize one attention state; committed delivery obligations survive restart; expired claims are recoverable; stale-generation receipts cannot overwrite new registrations. At-least-once network attempts can still cause repeated OS display around an ambiguous send/crash. Stable topics/tags, generic payloads, freshness limits and current-state navigation mitigate that; no end-to-end exactly-once claim is made.

## 15. Failure and race matrix

| Case | Required behavior |
|---|---|
| Transaction rolls back after inserting state/event/hint | No attention entry or push escapes the rollback. |
| Lower event ID commits after a higher ID | Its pending subject is still processed; no watermark loss. |
| Duplicate event, SSE replay or two tabs | One semantic entry/version; server badge and filtered membership converge. |
| Update arrives while old version is marked read | The new version remains unread. |
| Visible bulk read races a new item or same-item update | New data remains unread outside the acknowledged ID/version set. |
| Question answered while push is queued | Close Needs you and cancel/suppress the unsent wakeup. |
| Question expires while worker is down or during quiet | No expired question is presented as actionable when the app returns; no false answer or deadline extension. |
| Review edited/replaced/denied/task stopped | Old item is resolved/superseded; only actual current review can be acted on through its existing endpoint. |
| Grant revoked or target access removed before send/click | Recheck current source state and show the real access blocker; do not revive old authority. |
| Helper fails, manager repairs autonomously | Update the work; no duplicate owner failure storm for routine internal recovery. |
| Native turn ends but required results missing | No completed-result notification; use current work's honest failure/attention state. |
| Partial success plus uncertain external operation | Keep receipts and reconciliation links; do not present total completion or retry effects. |
| Multiple independent runs/triggers | Distinct work identities remain distinct; notification grouping grants no shared ownership. |
| Quiet period crosses midnight/DST/timezone change | Correct next allowed actual instant, no repeated end-of-quiet summary. |
| Burst/catch-up retries | Stable bounded summary identity, no self-merge, no lost inbox rows, no endless replay. |
| Worker dies before/after HTTPS send | Recover lease with fence; accepted-vs-unknown remains honest; no database/domain lock held over network. |
| Vendor accepts but device offline/OS blocks display | Record accepted only; retain inbox and let TTL bound relevance. |
| One device returns 404/410 | Retire only that subscription; other devices continue. |
| VAPID error, 429, 5xx or slow endpoint | Correct bounded permanent/transient handling and fairness; do not repeatedly delete valid subscriptions. |
| Malicious endpoint/redirect/DNS change | Public HTTPS validation/pinning blocks internal destinations; no secrets leak in logs. |
| Browser permission denied, unsupported or insecure origin | Full inbox works; setup explains the actual capability blocker without repeated prompts. |
| Cookie expires before push arrives | Generic visible notification; click requires normal login and current-state resolution. |
| Explicit logout/revoke races delivery | Server registration disabled; unsubscribe/close best effort; any in-flight alert remains generic and cannot authenticate. |
| Old push arrives after resolution or re-registration | Generic content, current safe navigation; old worker receipt cannot mutate new registration generation. |
| Service worker restarts/updates/no app tab exists | Visible push handler works without in-memory app state or authenticated prefetch. |
| Old/archived target is beyond current task/message page | Resolve by canonical reference; show history/closed status rather than a broken link or fresh task. |
| Pruning followed by reconciliation | No old unread resurrection; underlying work/receipts remain intact. |
| Laptop/Docker/network offline | No new-delivery uptime claim; bounded recovery on return and durable inbox history. |

## 16. Implementation sequence and exact reach

1. **Contract/source map:** inventory every relevant transition and existing action predicate; decide the shared bounded domain-status interface and lock order. Write focused cases for current question/review closure, accepted results versus turns, owner discussion, managed peer roots and operational pause reasons. Inspect the candidate stable pywebpush request-session API and installed browser capabilities. Freeze the generic-preview/local-only policy and setup requirements before exposing UI.
2. **Durable attention:** additive migration, transactional dirty-subject hints, material versions/read state, reconciliation/expiry/initial seeding and paginated owner APIs. Implement unread/count/filter consistency and version-aware visible-page bulk reads. No push or model invocation is needed to demonstrate this slice.
3. **Inbox and manager summaries:** Attention navigation, Needs you/Updates/History, read/unread/clear semantics, current source resolver, cross-conversation/thread deep links and deterministic manager summaries. Reuse exact existing answer/review workflows and test stale cards before adding push.
4. **Preferences/quiet scheduling:** owner preferences with revision validation, categories/timezone, quiet intervals, due-time reevaluation, burst control and stable catch-up identity. Run deterministic clock/DST/retry/pruning cases, including preference changes during claim and read-before-release.
5. **Push lifecycle/worker:** pinned dependency/configuration, private VAPID/subscriptions, least-necessary worker environment, constrained HTTP send, leases/generations, status classification/freshness, service worker/manifest, explicit enable/test/revoke and logout integration. Keep notifications independent of agent capacity and provider collection.
6. **Acceptance/review/docs:** real desktop push with explicit owner permission, closed-tab/restart/answer scenarios, multi-tab/UI checks, security/fault tests, cumulative affected regressions, final code review/fixes and source/private-state integrity. Archive only fixture work/subscriptions and keep original settings/keys/connections/volumes. Document device/network/transport coverage and update roadmap/checklist only from evidence.

Expected backend reach: `db.py`, shared bounded work/outcome/status readers, `inputs.py`, review/reconciliation helpers, worker/source transition hints, new attention API/projector/delivery modules, `auth.py`/session/logout integration, private setup/settings, one additive migration, `pyproject.toml`/`requirements.lock`, `compose.yaml` and Docker packaging only where necessary. Trace callers before moving shared predicates; do not modify Hermes runtime source or change tool access.

Expected frontend reach: `App.tsx`, `api.ts`, new attention/settings components using current panels, deep-link navigation coordination, scoped styles, service-worker registration/public worker script and minimal manifest/icons as needed. Do not add an all-purpose frontend notifications SDK or redesign unrelated screens.

Expected validation: focused backend notification/state/read/delivery tests; regressions around existing questions/reviews/outcomes, managed run status and archive/logout; controlled HTTP/clock/lease tests; browser scripts for inbox/settings/deep links/permission states and multiple tabs; an opt-in native acceptance driver with concrete fixture IDs, real owner decision and guarded cleanup. No broad benchmark/evaluation project is started by these tests.

## 17. Acceptance matrix and Phase 5 exit

| Area | Required evidence before closing 5.4 |
|---|---|
| Domain truth | Real pending question and all applicable review types link to current work; closure/expiry/replacement removes actionability. Accepted results, partial failure and uncertainty agree with existing work/result projection. |
| Durable unread | Reload, logout/login, two browsers/tabs, observed-version read, visible-bulk-read/new-update races and paging/timestamp ties preserve correct server counts and membership. |
| Manager summary | One useful summary per work/run, precise accepted/required counts and distinct exact decision links; helper chatter/retries do not create notification storms. |
| Queue correctness | Rollback, delayed lower-ID commit, duplicate hints, projector crashes, a new dirty generation during processing, stale lease completion and failed-subject fairness all pass. |
| Quiet/burst policy | Cross-midnight, DST gap/fold, explicit timezone, preference revisions, read/resolve during deferral, empty catch-up and repeated summary processing pass without stale flood or hidden priority bypass. |
| Push lifecycle | Owner enables this browser explicitly; actual encrypted Web Push reaches it with the app tab closed where supported; disable/logout/revoke/expired registration/re-registration/key generation behave correctly. |
| Security/privacy | Endpoint SSRF/redirect/DNS tests, malformed keys, unauthorized registration/read/revoke, no credential/payload/answer canary in logs/notification records/SW caches, same-origin navigation and stale click refusal pass. |
| Delivery honesty | Accepted is not labeled delivered/read; TTL/retry/404/410/401/403/429/5xx/timeout are distinguished; one bad device does not disable others; ambiguous-send duplicates are explicitly bounded, not falsely ruled out. |
| Browser/UI | Keyboard/screen-reader/focus, dark theme, zoom, long titles, 1440/768/390/320px, denied/default/granted/unsupported states, stale/archived deep links, reconnect and two-tab count/filter consistency pass. Headless simulations are labeled separately from real OS delivery. |
| Recovery/local integrity | Restart notification/API/worker components while attention is pending and during claims; original work/native/decision identity survives. Hermes source, private volumes, owner routines/groups/connections and unrelated subscriptions/preferences are preserved. |
| Retention/limits | Indexed/bounded scans, fresh queue saturation/catch-up, delivery history pruning, no unread resurrection and actionable item preservation pass. |

### Real local demonstration

Use one isolated acceptance group and a narrow local manager responsibility with two existing collaborators. Create real tool-backed contributions and accepted results. Prepare one real ordinary question or exact review, record it in Attention, close the browser tab and restart the relevant backend components while waiting. Have the owner explicitly enable browser notifications and grant the browser/OS permission when the concrete setup/test is ready; do not simulate this decision or ask for credentials in chat.

Demonstrate a real push that opens the exact current work after normal authentication. The owner answers/decides through existing controls; the same work/native context resumes, the stale action disappears, accepted results and their links appear, and no new duplicate occurrence/effect is created. Inspect the exact work/attempt/question/review/result/message identities and push-service acceptance evidence. Owner confirmation/visible OS evidence supports actual display; a server 201 or a mocked browser event alone does not.

Separately demonstrate quiet deferral/catch-up, read in another tab before send, denied/expired prompt, source setup problem, one invalid subscription and a stale notification click using controlled fixtures. Use no new external-provider mutation unless genuinely necessary; any such effect retains its ordinary exact owner decision policy. Keep native runs, synthetic event/delivery failures and actual live push evidence clearly labeled.

Afterward settle fixture executions, archive only test groups/routines/triggers, revoke only fixture grants/subscriptions and restore only preferences changed by the test. Do not delete/revoke the owner's existing subscriptions, connections, source credentials, VAPID key, private volumes or unrelated work. Keep historical artifacts/decisions/receipts and sanitized evidence.

**Phase 5 exit is a separate recorded check after 5.4:** scheduled and supported event-triggered manager work continues with no browser, preserves occurrence/native identity across duplicate wake/delivery and backend restart, survives a real approval/answer delay with current authority, and delivers usable results/attention. Reuse 5.1–5.3 evidence where unchanged; repeat affected paths after 5.4 changes and test the full composed flow. Record real provider event transport versus synthetic local events, machine-awake limits and all cumulative regressions. Do not mark all Phase 5 complete based solely on 5.4 UI or test count.

## 18. Decisions, limits and what this plan does not prove

Chosen direction: a durable, current-state attention view plus a PostgreSQL delivery queue and standards browser push, built in Hermbot above unmodified Hermes. No hosted notification engine, new workflow platform, LLM urgency classifier, extra notification channels, automatic per-item reminder/escalation scheduler or private lock-screen previews are necessary for this milestone.

The five record groups and one isolated notification worker are proposed implementation details, not code already shipped. The implementation must verify exact migration fields/indexes, the pinned stable push-library boundary and actual browser compatibility, then retain the simplest version that satisfies these contracts. Reuse existing helpers without importing their old coverage limits as new guarantees.

Planning cannot eliminate unknown defects or guarantee a user sees a notification. OS permissions, Focus settings, vendor availability, network conditions, browser versions, phone HTTPS reachability and machine sleep are outside a local app's complete control. In-flight push can outlive a source decision or logout; generic payloads and current-state authenticated navigation address its consequences. Budget, native-tool, external-effect and harness-integrity boundaries from prior phases remain in effect.

The preceding sections are the researched design baseline. Implementation evidence and remaining acceptance follow; the milestone is not closed solely from controlled tests.


## 19. Implementation and current acceptance — October 26, 2026

Implemented migration 017, a versioned current-state attention projector/API, canonical bounded work/file reads, shared owner-request eligibility, browser registration/settings, a generic visible service worker and a separate control-network notification worker. Five record groups separate attention, dirty generations, preferences, encrypted browser subscriptions and fenced delivery state. Transactional database triggers replace the proposed `db.event` allowlist: they cover direct SQL request closures and source transitions without replaying tool/stream events or relying on event sequence commit order. Exact expiry hints and a bounded level reconciliation repair missed/expired state. Projection and push dispatch use independent pumps so a slow vendor cannot hold the inbox.

The sidebar exposes Attention with separate unread and owner-action counts. Needs you/Updates/History, observed-version read/unread, visible-only bulk reads, safe informational dismissal, accepted-result counts/report/file links and exact current work/review/thread/settings navigation are implemented. Owner-menu Notifications contains category preferences, explicit IANA quiet hours, opt-in browser registration/test and current/other-browser revocation. An answered question stays as history without its old answer control. No notification authorizes a task or changes work ownership.

Push uses `pywebpush==2.3.0` through its public request-session hook and Hermbot's constrained pinned-address transport. The project’s existing dependency versions, including cryptography, are preserved. Stable VAPID keys were generated privately in `.env`; push remains off until actual owner enablement. Browser endpoints/auth/key material are encrypted; settings and delivery history expose safe metadata. Quiet-period and overflow summaries, current-member/freshness checks, rate/queue caps, TTLs, bounded retries, per-device errors, generation fences, explicit current-browser logout and 90-day tombstones/history are implemented. No private OS preview, silent push, model polling, new notification provider or Hermes source patch was added.

Verification: **638 backend tests pass**, with the existing 56 deprecation warnings; lint and the pinned production frontend/application build pass. Responsive real-browser checks cover 1440/768/390/320px, settings, dark theme, stale answered-question links and a real headless service-worker registration with no cached API data. The taste inspector passes with the narrowly documented existing-background/real-overlay allowances. [Machine-readable evidence](../test-results/phase54-backend.xml), [browser evidence](../test-results/phase54-browser.json), [review and scope](PHASE5-ATTENTION-REVIEW.md).

The actual native demonstration is `Phase 5.4 attention acceptance 9833c8d7160a`. Sage’s one real question survived API/worker restart; the owner answered through the application. Three manager attempts use the same native session. Scout and Quill executed real terminal calculations, published their two artifacts, and Sage read both and accepted the combined result. One result attention entry links the current work. The verifier checks exact identities, accepted contributions, file reads, terminal outputs and zero connected-provider mutations. [Native proof](../test-results/phase54-live.json). [Integrity](../test-results/phase54-integrity.json) confirms all 2,169 upstream Python files and all nine private bot volumes remain unchanged.

**Acceptance completed, October 26, 2026:** the owner enabled notifications for Chrome and subsequently confirmed a fresh exact-result push arrived with Hermbot tabs closed and opened the requested report. This is actual owner evidence, not HTTP acceptance or headless permission simulation. The separate composed exit verifies fresh native scheduled/signed-local-event managers, duplicate deliveries, active-attempt backend restart identity and revalidated actual owner-answer continuation. Guarded cleanup archived both fixture groups/routines and the exit event trigger/source and revoked both grants, preserving all original owner groups/bot settings/active routine, connections, browser registration/preferences/keys and private bot volumes. [Final evidence](../test-results/phase5-exit-final.json), [review and scope](PHASE5-ATTENTION-REVIEW.md#phase-5-composed-exit--october-26-2026). Phase 5.4 and Phase 5 are accepted for the local scope; connected-service coordination is next.

Owner confirmation and cleanup update, October 26, 2026: enabling notifications for Chrome resolved the missing-banner report; the owner confirms that notifications now work. At the owner's request, temporary browser-check/local-display controls and browser receipt instrumentation are removed. Normal push, registration, preferences and the original test button remain. The exact site-level versus OS setting was not specified. The subsequent owner-confirmed closed-tab current-result navigation, guarded fixture cleanup and separate Phase 5 exit now pass. This supersedes the earlier unregistered-browser/missing-display status. See C64 in [ISSUES.md](ISSUES.md).
