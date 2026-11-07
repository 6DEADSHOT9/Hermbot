# Phase 5.2 code review and corrections

What this change does: saved owner instructions can start ordinary teammate work from authenticated provider events. A separate collector commits deliveries and polling progress before acknowledgement; existing tasks, outcomes, permissions, reviews and native Hermes execution do the work.

Reviewed the connected source/trigger APIs, raw-body signatures, private credential paths, provider transport and normalization, cursor commits, lease fencing, admission, first claims, conversation/connection lifecycle hooks, shared routine execution and owner UI. Compared existing files against the pre-change source snapshot. The review was followed by the fixes below and regression checks; these are corrected findings, not remaining recommendations.

## Must fix — corrected

1. **An unchanged poll event could conflict with its own delivery identity** (`backend/trigger_worker.py`, `tests/test_event_triggers.py`).
   - **What this is:** Polls revisit an observed resource version after overlap or recovery.
   - **Problem:** Hashing its full normalized payload included a collection timestamp that could change on the next observation. The stable delivery ID then appeared to have different content, blocking the page/cursor transaction.
   - **Fix:** Hash provider plus logical event identity for polling; retain exact raw-body hashes for actual immutable webhook deliveries. A repeated Gmail history entry at different collection times now commits once without conflict.
   - **If skipped:** Routine polling could stop on legitimate duplicates and repeatedly retry the same page.

2. **Old queued events could bypass today's daily cap** (`backend/triggers.py`, migration 015).
   - **What this is:** Events received earlier can be admitted when a teammate becomes available later.
   - **Problem:** Counting the occurrence's creation day did not count yesterday's backlog as today's consumption.
   - **Fix:** Record and count `admitted_at` in UTC. The regression admits an older event today and checks that the daily allowance is consumed.
   - **If skipped:** A backlog could exceed the owner's configured daily admissions.

3. **Coalescing could pause too early or grow without a member limit** (`backend/triggers.py`).
   - **What this is:** Latest-state updates may share a pending occurrence for the same resource/type.
   - **Problem:** Checking pending capacity before reuse could reject an update that needed no new occurrence. Without a member bound, a single occurrence could retain excessive context before admission.
   - **Fix:** Reuse eligible occurrences before checking capacity; cap membership at 100 and retain the quiet/max-wait bounds. Tests exercise a full queue, same-resource reuse and the 101st update.
   - **If skipped:** Busy sources could pause needlessly or create oversized admission input.

4. **Blocked triggers could monopolize admission passes** (`backend/triggers.py`).
   - **What this is:** Each pass considers at most 100 eligible triggers.
   - **Problem:** Sorting solely by original receipt time would repeatedly select the same blocked triggers, starving later eligible work.
   - **Fix:** Rotate blocked occurrences with a short next-check time and order candidates by that eligibility time. The regression verifies that a blocked occurrence remains pending and moves behind eligible work.
   - **If skipped:** Work could stay pending despite available capacity elsewhere.

5. **Admission could reverse the conversation archive lock order** (`backend/triggers.py`, `backend/product.py`).
   - **What this is:** Archive locks the destination then pauses its triggers; admission locks a trigger before creating its task.
   - **Problem:** The ordinary task creation path takes a shared destination lock. Waiting for that lock while holding the trigger could deadlock against archive.
   - **Fix:** Acquire the destination share lock without waiting before creation; a locked destination leaves an inspectable pending reason. A concurrent regression holds the archive lock and proves admission returns without waiting.
   - **If skipped:** Archiving and background admission could block each other.

6. **Minimal provider records and signature text needed explicit handling** (`backend/trigger_providers.py`).
   - **What this is:** Calendar cancellations can omit an etag; a Gmail message can disappear between history collection and metadata lookup. Untrusted signature headers can contain non-ASCII text.
   - **Problem:** Assuming full objects could halt cursor advancement, and comparing unsupported string encodings could raise a type error.
   - **Fix:** Give sparse cancellations a stable fallback version, record inaccessible Gmail messages as ignored tombstones, and compare signature bytes. Identity mismatches are rejected without retaining foreign payloads. Regressions exercise these boundaries and stale Google cursors.
   - **If skipped:** One deleted object or malformed header could interrupt collection or return an unintended server error.

7. **Historical work links could follow a trigger's edited destination** (`backend/triggers.py`, `web/src/EventTriggers.tsx`).
   - **What this is:** A trigger may later deliver to another conversation, while its earlier results remain in the original one.
   - **Problem:** Using the current definition for old navigation and the original task for Stop could show or control the wrong execution after edits/retries.
   - **Fix:** Return the actual task conversation and authoritative current work execution in history. The UI uses those identities; a regression changes the trigger destination and verifies its earlier run still points to the original conversation.
   - **If skipped:** Owners could be sent to the wrong conversation or see an ineffective Stop action.

8. **Expected-result editing used a secure-context-only browser API** (`web/src/EventTriggers.tsx`, `web/src/Routines.tsx`).
   - **What this is:** Both forms generate a key when adding an expected result.
   - **Problem:** The copied code used `crypto.randomUUID`, which is unavailable on some plain-HTTP origins.
   - **Fix:** Reuse the existing `requestKey()` helper based on `getRandomValues`. The browser check adds results in both forms through `http://api:8000`.
   - **If skipped:** A normal editor button could fail before the owner could save their responsibility.

9. **The event worker's tmpfs YAML was parsed as several mount paths** (`compose.yaml`).
   - **What this is:** The read-only collector has a small writable temporary filesystem.
   - **Problem:** Commas in an unquoted YAML flow-list entry split its mount options into separate values; Docker rejected `nodev` as a mount path.
   - **Fix:** Quote the complete mount entry. Actual Compose startup and collector health passed afterward.
   - **If skipped:** The collector could not start despite passing Python tests and image build.

## Should fix — corrected

10. **Saved “ready” status could outlive a collector** (`backend/triggers.py`).
    - **What this is:** Source health is shown independently of occurrence status.
    - **Problem:** A stopped collector could leave the last stored successful state visible indefinitely.
    - **Fix:** Project stale monitored check-ins as disconnected, without rewriting durable source state. Test the stale projection and keep delivery gaps visible.
    - **If skipped:** Owners could mistake unavailable delivery for healthy listening.

Source archive now revokes its private ingress key and permits a fresh source for that scope while preserving history. Live source uniqueness also prevents two Socket Mode consumers for one Slack app. Owner previews validate only sample matching/access, not an unfinished execution form. History cursors require timestamp/identifier pairs. These lifecycle and boundary rules have targeted regressions.

## Initial local verification (before live provider canaries)

All **523 backend tests** pass, including **44 event-trigger test cases**. Ruff, TypeScript and the web image build pass. Real local acceptance produced one fulfilled report after concurrent duplicate delivery and API/collector restart. Four live read-only provider baselines passed. Chromium covered the forms, source credential privacy, preview, state controls, old/current histories, result links and four viewport widths. Taste inspected the live active dialog with background chat hidden only during measurement; the React tree remained intact. The accessible dialog h2 and real overlay shadow are explicit contextual exceptions, not page design failures. Screenshots and interaction/overflow checks used the running app. The complete [evidence and limitations](PHASE5-EVENTS.md) distinguish live checks from isolated fixtures.

Verdict: Ship the local implementation. Keep the provider-delivery acceptance items open until real source events are observed.

Not checked: live Slack Socket delivery, new Google/GitHub change canaries, public webhook ingress and production load. Native independently authenticated effects retain their existing receipt/review coverage limit. The existing frontend chunk-size warning remains.


## October 25 live-provider review and final corrections

What this change does: completes live provider acceptance and corrects the failures it exposed. Source collection still supplies quoted facts to ordinary work; Hermes source remains unmodified. Reviewed the changed callers, capability manifest/finish/settlement boundary, fixed provider endpoints, authenticated creation provenance, export handling, fenced page commit, resource-state retention, self-event recheck, daily allowance and the acceptance verifier/cleanup. Assumed the existing single-owner local deployment.

Corrected findings:

11. **Legacy finish accepted stale preparation before settlement rejected it.** Finish now returns an actionable 409 during the actual turn for a previously ready but expired legacy plan. Managed tasks and waiting reviews keep their existing paths. The regression refreshes preparation and then actually settles successfully. Four real canary tasks recovered through guarded report-only continuations; successful effects were not repeated. [C47](ISSUES.md).

12. **Drive server versions and conversion did not identify a distinct user change.** Record visible file state, skip unchanged server-only increments, and include that state in new mutation receipts. A fresh live Doc showed that conversion changes modification time too; the final correction therefore verifies the first observed approved plain text/name/folder using the private operation marker and successful creation receipt. The export runs outside transactions; its state-bound proof commits with the page and remains valid across invisible versions, while later edits remove correlation. Initial text already edited by the user remains external input. Bound Drive pages to ten items to keep possible exports within the fenced lease. Retained pre-correction data was reconciled only after a real read-only export, an exact state match and a no-admitted-task assertion. [C48](ISSUES.md).

13. **The daily cap prevented a no-work self-event decision.** Move finalized receipt classification before the daily admission limit, retaining the limit before actual work creation. A regression consumes the allowance and confirms a late own receipt still resolves to ignored with no extra task. [C49](ISSUES.md).

The acceptance driver now requires the explicit outside-Hermbot change marker and correct object identity, a real human Allow once decision bound to the exact action hash, settled receipt continuation, fulfilled event reports and no connector operations from those reports. Its submission retry keeps frozen arguments and a stable key. Source/trigger/group cleanup is guarded by full verification and preserves history and reusable private setup.

All **547 backend tests** pass, followed by the added proof-retention assertion (2 parametrized cases). Ruff and the application image build pass. No frontend source changed in this completion; prior browser evidence remains applicable. All five actual owner changes produced one completed report each. Slack message/mention callbacks share one occurrence; all five own-write checks pass. The converted-Doc live check exercised the actual collector read-only function against the already approved object, followed by normal admission. Original failures remain in history. [Evidence and precise limits](PHASE5-EVENTS.md).

Verdict: Ship the completed local Phase 5.2 milestone. No unresolved must-fix finding remains within its recorded scope.

Not checked: public webhook ingress, production load, every intermediate polling transition, Drive permission-only changes or rich-format-only edits before its initial plain-text comparison. Independently authenticated native effects retain their existing correlation coverage limit.
