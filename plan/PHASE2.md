# Phase 2 implementation and acceptance

Status: **complete for the approved local scope, verified on 2026-10-07**. Milestones 2.1–2.5, the local exit gate, and the requested code review with fixes and regression verification passed. The application is running at [localhost:8780](http://localhost:8780). Phase 3 has not started.

The owner clarified on 2026-10-07: “for now we will run the system on this machine only. this will be discussed later.” Linux staging and HTTPS deployment are therefore deferred by explicit user instruction. Phase 2 acceptance targets the local Docker application; this does not claim a staging deployment.

| Milestone | Implementation | Acceptance |
|---|---|---|
| 2.1 Owner onboarding | Revocable opaque sessions; encrypted provider settings; timezone, defaults, permissions and limits | Passed: backend authentication/configuration checks, browser settings save and fresh login |
| 2.2 Bot lifecycle | Dynamic registry, supervisor provisioning, explicit clone state, archive/restore/delete, runtime controls | Passed: real native lifecycle and browser create/edit/clone/archive/restore/delete; partial clone recovered using its original operation |
| 2.3 Conversations and groups | Attachments, separate thread sessions, reactions, mentions, skill references, group editing, queued edits, steering, retries and inspection | Passed: native attachment/skill tools and browser execution controls; retained deleted-bot chats and older outputs remain accessible |
| 2.4 Bot-managed lifecycle | Scoped owner grants, exact lifecycle proposals, stale-review rejection, durable approved execution | Passed: UI-created manager obtained owner approval for a specialist and incorporated its real output; rejection, expiry/revocation, stale proposals, budgets and recovery tested |
| 2.5 Product shell | Responsive three-pane local UI with bot state and settings controls | Passed locally: fresh desktop/mobile browser sessions, accessible settings/navigation, no horizontal overflow or script errors; Linux/HTTPS deferred |
| Phase 1 regressions | Original coordination/recovery tests retained; real collaboration and worker restart repeated | Passed: research/writing, autonomous arithmetic, dependent code/tests manager and single-attempt recovery during native terminal execution |
| Code review after implementation | Authorization, concurrency, provisioning/recovery, UI and private data handling | Complete: findings fixed and retested; details in [PHASE2-REVIEW.md](PHASE2-REVIEW.md) |

## Acceptance evidence

All files below are under `test-results/`. Synthetic failure-path tests are separate from real Hermes/model acceptance.

| Evidence | Result |
|---|---|
| `backend.xml` | 51 backend tests passed, including the original Phase 1 suite; zero failures/errors, isolated `hermbot_test` database |
| Static checks and production build | Ruff, TypeScript and Vite passed; final frontend contains 318 modules |
| `phase2-browser.json` | Owner created a manager and management grant, approved the exact specialist proposal once, and received a real collaborative result through the web UI |
| `phase2-controls.json` | Settings save, clone with all four state choices, avatar edit, archive/restore and typed-name deletion passed |
| `phase2-work-controls.json` | Queue, edit, redirect, inspect, stop before dispatch and retry passed through the UI; retry completed with a real artifact; reactions and thread controls passed |
| `phase2-lifecycle.json` | Idempotent bot creation, native attachment read, private workspace output, separate thread, same-operation partial-clone recovery, cloned-file verification, runtime stop/start, archive/restore and deletion passed |
| `phase2-skills.json` | 53 native installed skills discovered; selected `hermes-agent` skill loaded by actual `skill_view`; mentions and group membership edits persisted |
| `phase2-history.json` | Deleted teammate's saved chat and artifact preview remained readable; new work and editing were disabled |
| `live-acceptance.json` | Real named writing delegation, autonomous data verification (94), and manager with dependent code/test handoffs passed |
| `active-worker-recovery.json` | Worker restarted during an actual native terminal command; the original task completed with one execution attempt and native run |
| `browser-reconnect.json`, `desktop.png`, `mobile.png` | Fresh opaque authenticated session recovered saved tasks and artifacts; desktop/mobile layout, theme and settings access passed |
| `phase2-completion-audit.json` | Final read-only audit passed: five healthy compatible runtimes on the final image, 15 isolated private volumes, original seed volumes preserved, worker unpaused, zero pending operations and unsettled tasks |

The web exit-gate task is `4a7052ed-c57a-4226-a2dc-2986df3632b9`. The manager and specialist remain available as **Phase 2 UI manager** and **Phase 2 UI specialist**, alongside Scout, Quill and Sage. Temporary lifecycle/skills acceptance bots were deleted through owner APIs; their saved chats remain under **Past teammates**. The final audit read back collaborative artifacts and verified their checksums.

Native runtimes use `hermbot-hermes:phase2`, image `sha256:991414470d93af621e35b32d1000ddb54714039673b3b4d69d800871d48631ec`, based on pinned Hermes revision `749220ef0007f8d87bd1531f1c24b0fe93816385`. The native skills route needs a guarded compatibility patch for this pinned version; fresh profiles seed bundled skills before selection. API/test image names retain their historical `phase1` suffix for script compatibility; they contain the current implementation.

## Scope and operating limits

OpenAI-compatible provider configuration is implemented first. Codex and Claude subscription adapters remain follow-up work. Lifecycle provisioning is owner controlled; managers propose only granted create/configure/start/stop actions, and exact owner approval is required before execution. Deletion, provider credentials and access policy remain owner-only. Clone state is explicitly selected; browser state and credentials are not copied.

The local app retains bounded execution/resource limits, one native execution per bot, two-to-six-member groups, and attachment limits documented in the project [README](../README.md). There is no claim of remote deployment, Phase 3 computer takeover, connector integrations or subscription login. The full original roadmap and archival sources remain unchanged.

Model and service credentials remain private; they are not recorded in acceptance documents. Existing bot volumes and native histories are preserved while moving the fixed Phase 1 roster under supervisor ownership.
