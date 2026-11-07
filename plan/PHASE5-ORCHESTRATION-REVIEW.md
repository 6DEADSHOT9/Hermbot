# Phase 5.3 code review and corrections

What this change does: an owner can schedule or event-trigger an accountable manager with explicitly permitted collaborators. Each occurrence uses existing durable work, real native Hermes execution, contribution acceptance, human decisions and connector receipts under a shared execution allowance.

Reviewed the optional model contract, additive migration, saved-work admission, current-grant intersection, delegation/reassignment, peer replies, owner revisions, claim/revoke locks, budget pause/continuation, final acceptance, incremental windows, inspection and the two configuration forms. Existing files were compared against the source-only before snapshot at `/private/tmp/hermbot-phase53-before`; this workspace has no Git metadata. Assumed deployment is the existing single-owner local stack with three initial bots, multiple workers and concurrent owner/API actions. This was a focused review of the change and its connected paths, not a claim that the old issue backlog is fixed.

## Must fix — corrected

1. **Workspace state failed before native acceptance began** (`backend/app.py`).
   - **What this is:** `/api/v1/state` supplies the running workspace.
   - **Problem:** Task-specific scope inspection was inserted before any task variable existed. The first live acceptance attempt received HTTP 500 and created no fixture work.
   - **Fix:** Keep selected-scope enrichment in task inspection. Added an authenticated state/inspection API regression and verified the deployed endpoint.
   - **If skipped:** The workspace cannot load even though the underlying runtimes are healthy.

2. **A cosmetic work classification could detach recurring accounting and authority** (`recurring_management.py`, migration 016 and connected callers).
   - **What this is:** Owner correction of a message association changes display/work links without stopping execution.
   - **Problem:** Looking up scope and budget only from mutable `work_item_id`/`message_intent` would let an already-admitted automated execution lose its run binding, overlap guard or stop coverage.
   - **Fix:** A static indexed `management_work_item_id` keeps admitted automated execution attached to its occurrence. Authority, budget, locks, continuation, overlap and whole-run cancellation use it. Ordinary display classification remains independent. Tests exercise null/reclassified links, shared consumption, continuation and peer cancellation.
   - **If skipped:** A classification correction could reset allowance or make execution appear ungranted or detached.

3. **Cancel/steer/reassign accidentally required inspect permission** (`service.py`).
   - **What this is:** Descendant ownership validation is shared with the inspection path.
   - **Problem:** A useful selected subset such as delegate+cancel passed grant validation but the ownership helper demanded inspect before permitting cancellation.
   - **Fix:** Validate the actual requested operation while retaining the same descendant boundary. Default inspection still requires inspect. A regression permits selected cancel and rejects unselected inspection.
   - **If skipped:** The form advertises an operation that its backend refuses for the wrong permission.

4. **Manager prose could be mistaken for a successful recurring execution** (`worker.py`, `routines.py`, `recurring_management.py`).
   - **What this is:** A native turn may finish without accepted required outcomes.
   - **Problem:** Native `completed` alone could leave an occurrence's execution successful while its work still needed attention. A failure counter based only on the original root also retained a failure after successful owner-directed repair.
   - **Fix:** Missing accepted required results make a settled manager completion fail explicitly. Final acceptance waits for optional automated execution too. Definite-failure checks examine current roots, accepted requirements and unsettled work. Equivalent-grant incremental cursors require verified results and settled attempts/effects.
   - **If skipped:** Schedule history, failure pausing and incremental windows could misrepresent actual delivered work.

5. **Managed peer admission and revocation needed a consistent lock boundary** (`coordination.py`, `service.py`, `app.py`, `worker.py`).
   - **What this is:** Dispatch validates current authority while revocation stops owned work.
   - **Problem:** Holding grant authority before waiting for the occurrence work lock can invert revocation's work-then-grant order. Separate peer roots also require the same authority and stop coverage.
   - **Fix:** Managed inbox admission first claims work/root with nonblocking locks, then validates the current grant; claim/revoke paths use the same canonical work identity. Generated replies inherit scope/run/thread/budget. Regression checks concurrent revoke/claim ordering and cancellation of peer execution; native demonstrations confirm ordinary execution still progresses.
   - **If skipped:** Revocation and automated reply admission can contend in opposite orders or leave detached work.

The initial browser fixture also needed to await workspace/preview updates and match wrapped select labels. Its first event attempt used a colon in a Standard Webhooks delivery ID; the existing signature validator correctly refused it. Corrected the fixture to use a permitted stable ID and retry the same fixture source/trigger without duplicating an admitted event. These were acceptance-driver errors, not provider failures or reasons to weaken validation. The API regression test was aligned with the suite's existing non-lifespan client so it does not close the shared isolated test pool. Final cleanup initially called a nonexistent POST conversation-archive route (HTTP 405); corrected that acceptance driver to reuse the existing DELETE group endpoint. The subsequent guarded cleanup passed and preserved all original owner groups.

## Verification and completed acceptance

- [581 backend tests](../test-results/phase53-backend.xml) pass, including 34 new recurring-management cases; Ruff passes and the TypeScript/production build succeeds.
- [Real browser inspection](../test-results/phase53-browser.json) checks owner grant creation/selection, keyboard operations, invalid drafts, expiry warning, manager changes and four viewport sizes. No failing rule or horizontal overflow was found; screenshots were visually inspected. Existing clock-format lint warnings are benign.
- [Native proof](../test-results/phase53-live.json) verifies two scheduled manager runs, one signed-local-event run and the actual owner-answer continuation: eight actual contributor calculations, eight published artifacts, manager reads, accepted contributions, unique work/thread/session identity and one persisted final report per occurrence. These fixtures created no connector operations.
- [Restart/answer proof](../test-results/phase53-restart.json) confirms the original question/first attempt survived restarts, followed by one actual authenticated owner answer and continuation in the same native session. A later genuine contribution wait/resume completed normally. All three manager turns settled, both contributions were accepted and one final report was delivered in the correct thread.
- [Source/runtime integrity](../test-results/phase53-integrity.json) verifies 2,169 unchanged upstream Hermes Python files, all three bots' protections and nine preserved private volumes. Original owner bot configuration, groups and active routine definitions were compared with the before snapshot. All fixture schedules/triggers, the local fixture source and acceptance group are archived; fixture-created grants are revoked. Historical work, artifacts and decisions are preserved. Final cleanup confirms the original owner bot configurations, groups and active routine definitions still match the baseline.

Verdict: Ship. The implementation passes its corrected code checks for the inspected local scope. The actual owner-answer continuation and guarded final fixture cleanup now pass; Phase 5.3 is accepted for the local scope.

Not checked: no broad evaluation suite, public hosting, new live-provider mutation, every possible crash interleaving or independent native subprocess usage measurement. Existing connector-only review coverage, in-flight budget margin and local availability limits remain explicit.
