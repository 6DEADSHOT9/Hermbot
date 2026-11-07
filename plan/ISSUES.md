# Engineering issue and learning register

Created: 2026-10-14. Maintainer: the developer handling each investigation or change.

**Owner sequencing decision, 2026-10-19:** implement the current fix backlog after baseline evaluations, so prioritization includes newly uncovered issues as well as those already recorded. Then rerun evaluations against that baseline. [Evaluation work](EVALUATIONS.md) remains deferred until after intervening tasks; it is not replaced by starting this backlog first.

This is the canonical issue record: chronological usage history, historical fixes, superseded designs, current findings, causes, proposed approaches, verification and remaining limits. [Original-chat coverage](CHAT-COVERAGE.md) maps all 152 human turns in the three scoped original transcripts; [verification metadata](../test-results/issue-history-verification.json) records sources and counts.

Read [the chronological history](#chronological-issue-history) first. [Detailed records and maintenance](#detailed-records-and-maintenance) retain the current findings and their closure conditions. A passed milestone does not close a later defect. The register is manually maintained; automatic collection of every runtime failure is not implemented.

## Chronological issue history

Backfilled and verified on 2026-10-14 against the original local Codex transcripts (including earlier assistant diagnoses), the archived planning conversation, all repository review tables, phase reports and saved diagnostic/acceptance evidence. The detailed records later in this file retain causes and proposed closure checks for the current incident.

**Ordering:** oldest stage first, using documented dates where available and the order of the user’s reports otherwise. Date ranges are ranges, not exact incident dates. Same-stage review findings without timestamps retain source order. The current Manik incident is ordered by earliest identifiable occurrence/message, with approximate ordering explicitly indicated. Recording a later diagnosis here does not change when the underlying failure occurred.

**Status:** historical verification applies to its recorded build and checks. “Reported; exact diagnosis unavailable” is deliberately not marked fixed. Proposed approaches are not implemented changes. Repeated symptoms are linked to previous entries without asserting that they have the same root cause. Existing evidence is indexed, not rerun by this documentation change. No credentials or full private transcripts are copied.

Coverage includes owner usage failures, integration failures exposed by acceptance, review-discovered defects, external constraints, incorrect diagnoses and fixes later superseded. General feature requests and explanatory questions are recorded as scope decisions where relevant, not invented as bugs. The original transcripts have now been located and read. [CHAT-COVERAGE.md](CHAT-COVERAGE.md) accounts for each human turn, including follow-ups and contextual questions; [verification metadata](../test-results/issue-history-verification.json) records source identities and bounded coverage. Earlier assumed evidence gaps were corrected where those transcripts supply the diagnosis. A retained diagnosis is evidence of what was found then, not automatic proof the issue is fixed today.

### 2026-10-06 — Planning workflow before application implementation

#### C00 — Plan mode would not turn off in the original planning thread

- **Status:** Workflow recovered by starting the implementation thread; underlying Codex behavior not diagnosed.
- **Issue/cause:** The owner reported that plan mode was not turning off and directed implementation from a new thread. This was a tooling/workflow obstacle, before Hermbot usage, rather than an application defect.
- **Approach and outcome:** Preserve the original plan and transcript, transfer context and begin implementation in the next thread.
- **Evidence:** Archived planning thread P-U13, 2026-10-06 07:42:12 UTC; [original readable conversation](archive/conversation.txt), [archive manifest](archive/manifest.json).

### 2026-10-06 — Phase 1 integration and recovery

#### C01 — Initial collaboration tools were not directly available

- **Status:** Historically corrected
- **Issue/cause:** Hermes deferred plugin tools by default.
- **Approach and outcome:** Expose the small collaboration toolset directly.
- **Evidence:** [Phase 1 report](PHASE1.md); [live acceptance](../test-results/live-acceptance.json).

#### C02 — Native session creation conflicted on titles

- **Status:** Historically corrected
- **Issue/cause:** Session titles needed to be unique within a runtime.
- **Approach and outcome:** Use session-specific titles.
- **Evidence:** [Phase 1 report](PHASE1.md).

#### C03 — Frontend build exhausted machine memory

- **Status:** Historically corrected
- **Issue/cause:** An automatically resolved Rollup version exhausted memory on this machine.
- **Approach and outcome:** Pin the verified build dependency rather than depending on the newest resolution.
- **Evidence:** [Phase 1 report](PHASE1.md); [runtime/build documentation](../README.md).

#### C04 — Request ID generation failed on plain HTTP

- **Status:** Historically corrected
- **Issue/cause:** Browser request-ID generation depended on functionality unavailable on the local plain-HTTP origin.
- **Approach and outcome:** Generate IDs with cryptographically generated bytes compatible with that environment.
- **Evidence:** [Phase 1 report](PHASE1.md).

#### C05 — Long group list overflowed mobile navigation

- **Status:** Historically corrected; later expanded by C06/C07
- **Issue/cause:** The initial sidebar could not accommodate a long group list.
- **Approach and outcome:** Scrollable groups with reachable owner controls; later roster redesign addressed competition among sections.
- **Evidence:** [Phase 1 report](PHASE1.md); [UI review](UI-REVIEW.md).

### 2026-10-07 — Phase 2 lifecycle, state and permissions

##### Documented review findings: P2

The following individual findings and corrections are preserved from [PHASE2-REVIEW.md](PHASE2-REVIEW.md). They were discovered during implementation, acceptance or review; they are not all owner-reported production failures. Status is **historically corrected within the source review’s scope**, unless a supersession is noted below. Within this dated stage, the review did not retain exact discovery timestamps; source order is retained rather than inventing them.

| ID | Observed issue / cause | Correction actually recorded | Verification recorded |
|---|---|---|---|
| P2-01 | Attachment extraction could follow a bot-created workspace symlink. | Stage in a root-owned image directory; install with directory descriptors and `O_NOFOLLOW`, checksum verification and atomic replacement. | Symlink-directory/leaf regression test, native attachment read and clone proof. |
| P2-02 | Clone helpers initialized new data-volume roots with root ownership. | Set both target mount roots to the native Hermes user before copying selected state. | A real partial clone was recovered and its native runtime became healthy. |
| P2-03 | Raw SQLite copying could lose committed WAL data, and read-only source mounts could prevent creation of SQLite's WAL index. | Pause the source; copy database/WAL into isolated scratch storage; use SQLite backup into the target. | Workspace/history clone, native file verification, four-option browser clone and healthy native history startup. |
| P2-04 | Failed or ambiguously created clone helpers could prevent retry or hold deleted volumes. | Validate full ownership labels, resume created helpers, recreate settled failed helpers and remove owned helpers during deletion. | Same-operation recovery of the failed partial clone; confirmed deletion of acceptance volumes. |
| P2-05 | Provisioning reported success immediately after container creation, including a runtime that was failing to start. | Wait for Docker runtime health; preserve partial state and return actionable failure details. | The startup failure was reported honestly and recovered; all successful lifecycle evidence waits for healthy compatibility. |
| P2-06 | A restarting runtime could fail deletion because the stop check covered only `running`. | Stop both running and restarting containers before removal. | Restarting-container regression and recovery of the original failed delete operation. |
| P2-07 | Rebuilt image tags could reuse a container created from old image contents. | Include the resolved image identity in the configuration fingerprint. | Retained bots upgraded through owner actions while preserving their original volumes. |
| P2-08 | A lost provisioning receipt could repeat a completed lifecycle effect after supervisor recovery. | Persist payload hashes/results atomically on a dedicated supervisor volume, fsync file/directory, replay receipts, and clean clone helpers on replay. | Receipt replay rejects changed payloads; ambiguous transport reconciles the same operation ID. |
| P2-09 | Approved creation retry incorrectly spent or checked a second creation budget; a settled manager could reuse old authority. | Recheck live authority without consuming another creation, reject settled manager operations and stale/source-busy retries. | Approved failed-create retry and settled-parent rejection tests; partial-clone live recovery. |
| P2-10 | Provider/timezone changes could unarchive a bot, and saved timezone changes were not applied to runtimes. | Freeze prior status/desired state in the outbox; preserve archive on configure; queue between-run timezone configuration. | Archive-preservation and timezone/busy-work regression tests. |
| P2-11 | A renamed provider could be reimported from `.env` after restart. | Record the initial environment import and preserve owner-edited profiles. | Bootstrap-after-rename regression. |
| P2-12 | Pending work could be admitted while lifecycle configuration was taking effect; some lock orders could form cycles. | Fence task/delegation admission with a shared bot lock using `NOWAIT`; use workspace-before-source order for clone, root-before-grant for revocation, and nonblocking target locks for exact lifecycle approval. | Concurrent admission regression, original worker/grant tests and live collaborative regressions. |
| P2-13 | A duplicate task request could fail after its bot was subsequently stopped. | Resolve the original immutable task receipt before checking admission for new work. | Duplicate receipt after runtime-stop regression. |
| P2-14 | Group membership edits could remove a teammate with uncertain execution; approved specialist creation could exceed the group cap. | Block removal while native work is unresolved and check the six-member limit at approval. | Uncertain-membership and full-group specialist-approval regressions. |
| P2-15 | Readiness from a prior configuration could appear current during provisioning. | Clear readiness when queuing lifecycle changes and wait for the applied revision in browser acceptance. | Edit/archive/restore browser sequence and readiness-clear regression. |
| P2-16 | Settings forms submitted database-only fields, and some browser state survived retired bots, pagination, session expiry or changed conversations. | Send explicit settings fields; guard repeated settings submissions; recover active conversations and expired login; scope uploads to their original conversation; reset pagination and streamed text between runs. | Browser settings save/lifecycle controls, mobile reconnect and frontend compilation. |
| P2-17 | Deleting a bot hid its retained direct chat, and state windows could hide old unsettled work or conversation outputs. | Add read-only past-teammate navigation, preserve unresolved work in the state projection, and load all outputs for the selected conversation. | Retired-history browser acceptance and regression tests beyond the recent task/artifact windows. |
| P2-18 | The pinned native skills route passed unsupported `include_editorial`; skill enumeration's `skip_disabled` semantics were counterintuitive, and brand-new profiles had not yet seeded bundled skill files. | Apply a guarded pinned-source compatibility fix; include disabled skills in the owner catalog; run native idempotent skill synchronization before validation and enablement. | Runtime catalog enumeration and native selected-skill acceptance. |



**Supersession:** P2-18 used a pinned-source compatibility patch. That historical approach was later rejected by the owner’s unmodified-harness requirement and replaced in C18/NATIVE-02. Do not copy that old fix into new implementation. Local-only operation was an explicit owner scope decision; Linux/HTTPS staging was deferred, not silently claimed complete.

### 2026-10-07 — Phase 3 files, computer control and reviews

##### Documented review findings: P3

The following individual findings and corrections are preserved from [PHASE3-REVIEW.md](PHASE3-REVIEW.md). They were discovered during implementation, acceptance or review; they are not all owner-reported production failures. Status is **historically corrected within the source review’s scope**, unless a supersession is noted below. Within this dated stage, the review did not retain exact discovery timestamps; source order is retained rather than inventing them.

| ID | Observed issue / cause | Correction actually recorded | Verification recorded |
|---|---|---|---|
| P3-01 | An upload accepted spaces and Unicode in a filename, but the supervisor attachment schema rejected them at dispatch. | Use one plain-filename contract through upload, attachment transport and secure installation; reject `.`/`..`, path separators and control characters. | Regression cases include `résumé 2026.csv`, dot filenames, checksums and descriptor-based installation. Real native library and Phase 2 attachment runs passed. |
| P3-02 | Office XML entity checks could miss UTF-16/UTF-32 declarations; compressed content could consume excessive preview memory. | Check normalized declaration bytes before parsing, bound total decompression to 2 MB, limit selected sheets and cap preview text. | UTF-16 DTD/entity and decompression regressions, real DOCX/XLSX extraction and original-byte downloads. |
| P3-03 | Link import could reach private infrastructure through redirects or DNS changes, and a continuously dripping response could defeat socket timeouts. | Validate all resolved addresses at every redirect, connect to the validated numeric IP, verify TLS against the original hostname, and avoid environment proxies. Bound redirects/bytes and interrupt response headers/body at a wall-clock deadline. | Private-address, redirect, authentication/port and bounded-response tests; an actual dripping HTTP server was stopped by the deadline; actual public-link import. |
| P3-04 | Chromium blocked PDF previews inside a sandboxed frame. | Serve PDF with fixed `application/pdf`, `nosniff` and same-origin framing policy so the built-in viewer works. Other content retains the restrictive sandbox policy. | Fresh Chromium preview with actual rendered PDF text, downloaded bytes and content headers. |
| P3-05 | Native memory writes could follow unsafe directories/files or hang on a FIFO; concurrent CAS could overwrite another edit. | Use directory/file descriptors with `O_NOFOLLOW`, require regular UTF-8 files, use nonblocking file opens, and serialize writes with a cross-process lock and atomic fsynced replacement. | Symlink, stale-hash, same-content replay and concurrent-writer regressions. Actual memory edit/removal/restoration and immutable history. |
| P3-06 | Invalid bot configuration could be rejected after native knowledge had already changed; a skill description could have a non-string YAML value. | Validate the complete future configuration before the native write; require a matching name and nonempty string description. | Invalid-permission/configuration and frontmatter regressions; real skill import, enable, versions and removal. |
| P3-07 | Hermes caches memory/skill configuration, so saving disk content alone did not guarantee the next model run saw it. | Queue a durable runtime refresh, clear readiness, and resume approved learning only after its definitive receipt. Replay native writes with the same frozen CAS after uncertain transport. | Learning outbox, stale revision/hash, failed refresh, cancellation/revocation and busy-runtime regressions; actual `skill_view`, owner-edited learning, refresh and continuation. |
| P3-08 | Edited review drafts could reuse an approval for different content; model proposals could invent their own expected hash/revision or cross-bot authority. | Replace the approval with a new exact payload/hash and supersede the old one. Freeze native hashes/revisions on the server; recheck current management authority before application. | Old-decision HTTP 409, expiry/cancellation/grant regressions, real learning review and generic review UI. |
| P3-09 | Generic “Allow once” wording did not tell the model that requested secure input was finished. | Give secure input its own metadata-only schema, computer link and “Input completed · continue” action. Require explicit handback, then say completion is acknowledged without returning input. | Secure metadata/handback regression and successful real manager/Scout/Quill exit task. |
| P3-10 | Takeover could enable human input before an already admitted native tool finished. | Set human ownership first, then wait for the shared tool-execution mutex; input becomes ready only for the matching native lease epoch/viewer. Later tools pause and the task waits durably for explicit handback. | Real foreground terminal test: takeover waited 8.136 seconds, effect count stayed one, and the native turn paused/resumed. |
| P3-11 | An unavailable adapter or recreated runtime could leave database dispatch unaware of human ownership. | Commit the database admission fence before native takeover, recheck it during claim/dispatch and native tool context, and preserve it until explicit handback. | Unreachable-adapter database-fence, pending dispatch and durable pause regressions. |
| P3-12 | Status/control and desktop mutation requests could race between owner sessions. | Serialize status, takeover, handback, start, stop and browser launch with the same per-bot advisory lock. Native input remains independently epoch fenced. | Concurrent stop/takeover regression, live competing-session/stale-handback/reclaim checks. |
| P3-13 | If the desktop stopped unexpectedly during human control, the UI hid handback along with running-desktop buttons. | Keep explicit handback available even when the desktop is stopped. | Injected a real native desktop stop while a human held control; owner UI released both native lease and database fence, then restarted the desktop. |
| P3-14 | A late knowledge/file response could populate or close a panel after switching teammates/conversations. | Scope callback generations to the panel opening and target; retain the original operation while suppressing stale draft, file-chip, error and busy-state updates. | Browser held real skill/file-use responses across closing and changing targets; no stale draft or attachment appeared and the new panel stayed open. |
| P3-15 | New group selection could reset to an older chat before its metadata reached the cache. | Refresh the conversation roster before selecting the new group, so active-conversation recovery sees the new ID. | Real UI group submission and active browser-disconnect/reconnect acceptance. |
| P3-16 | Native desktop startup inherited the wrong home/runtime directory; Chromium's old-host singleton files blocked a persisted profile after recreation. | Initialize private directories/ownership before dropping to UID 10000, set the native home/XDG runtime, and remove only verified ephemeral Chromium singleton/port files during startup. Retain all profile/session data. | Real desktop/browser startup and owner lifecycle recreation with the same private volumes and a persisted cookie; cleared secure-input form stayed cleared. |
| P3-17 | Screen input could outlive an owner session or silently release control on disconnect. | Authenticate same-origin WebSockets, keep private native keys server-side, recheck session validity, bound traffic and filter native RFB input by lease epoch/viewer. Disconnect retains human ownership. | Unauthenticated/cross-origin socket tests, actual noVNC keyboard input, reconnect, competing owner sessions, epoch fencing and explicit handback. |



**Later finding:** P3-10 verified safe takeover with one foreground tool, but its exclusive tool mutex later caused the parallel start-order deadlock in C31/H01. Preserve both the successful narrow check and the later failure; the original test did not cover parallel reversed entry.

### 2026-10-07 — Owner-reported knowledge dialog and roster problems

#### C06 — Memory & skills dialog scrolled horizontally into empty space

- **Status:** Verified within recorded viewports; same issue as H03
- **Issue/cause:** The completed-workflow selector expanded to its longest title, and a later generic modal rule overrode desktop width.
- **Approach and outcome:** Constrain controls and wrapping; restore intended responsive dialog width.
- **Evidence:** [UI review](UI-REVIEW.md); [measurements](../test-results/ui-regressions.json).

#### C07 — Three noncollapsible roster sections hid groups

- **Status:** Verified within recorded viewports; same issue as H03
- **Issue/cause:** Separate fixed-height scroll areas competed for limited sidebar height; footer could escape short screens.
- **Approach and outcome:** Collapsible remembered sections, temporary search expansion, one roster scroll region and retained footer.
- **Evidence:** [UI review](UI-REVIEW.md); [measurements](../test-results/ui-regressions.json).

##### Documented review findings: UI

The following individual findings and corrections are preserved from [UI-REVIEW.md](UI-REVIEW.md). They were discovered during implementation, acceptance or review; they are not all owner-reported production failures. Status is **historically corrected within the source review’s scope**, unless a supersession is noted below. Within this dated stage, the review did not retain exact discovery timestamps; source order is retained rather than inventing them.

| ID | Observed issue / cause | Correction actually recorded | Verification recorded |
|---|---|---|---|
| UI-01 | A completed-workflow dropdown sized itself to its longest title, extending beyond the dialog. The management dialog's intended desktop width was also overridden by a later generic modal rule. | Constrain selects to the content width and give management dialogs their intended responsive width. Wrap long content and constrain skill cards. | Actual native knowledge and completed workflows at 1440×900, 390×844, 320×568 and 844×390. Dialog client/scroll widths match at 648/648, 356/356, 286/286 and 648/648 pixels. Memory editor and File library also have no dialog or page horizontal overflow. |
| UI-02 | Teammates, past teammates and groups competed for fixed heights in separate scroll areas; the footer could extend below short viewports. | Add independent accessible section toggles and remember their state in browser storage. Past teammates starts collapsed. Search reveals matching sections temporarily. Use one roster scroll area and a fixed-size footer. | Toggle all three sections, reload preferences, search collapsed groups, reach the last of 37 actual groups, and verify File library and Workspace settings remain within all four tested viewports. |
| UI-03 | The work panel initially covered chats on compact screens without a dismissal backdrop. | Start it closed on compact screens, close it when resizing into compact layout, and support backdrop/Escape dismissal. | Fresh mobile session, desktop-to-mobile resize, explicit opening, backdrop and Escape. |
| UI-04 | Loading a conversation briefly displayed the empty-chat welcome. | Show an explicit loading notice and a retry action for failed initial loads. | Hold a real conversation request and verify the welcome is absent while loading. |
| UI-05 | Late pagination responses could insert history from another chat; thread pagination could hide the main chat's earlier-message control. A late successful send could clear a newly selected conversation's draft. | Scope response updates to the conversation/thread generation, reset pagination when closing a thread, and preserve a draft edited while submission is pending. | Delayed pagination across chat switching, thread paging/close, and delayed submission across switching. Controlled GET responses and an intercepted POST exercise the frontend without native execution or model charges. |
| UI-06 | Header symbols and adjacent task actions were unclear or crowded. | Use existing icon components for Memory & skills/Computer, truncate long titles, and wrap action rows with consistent gaps. | Production build, screenshots, responsive checks and existing browser regressions. |



UI-01/UI-02 provide the detailed review records for C06/C07; they are cross-references, not additional incidents.

### 2026-10-07 — Sharing boundary and desktop memory error

#### C09 — Sharing the app with colleagues was not yet a supported deployment

- **Status:** Deferred scope, not a fixed defect
- **Issue/cause:** The user asked how colleagues could access a machine-local single-owner application.
- **Approach and outcome:** Keep local operation as explicitly requested; multi-user sharing and hosted HTTPS remain separate roadmap work.
- **Evidence:** [Roadmap decisions](README.md); [checklist](CHECKLIST.md).
- **Limit / learning:** No historical support answer establishes that hosting or multi-user isolation was implemented.

#### C08 — Desktop refused to start: 1,378 MB free, 1,536 MB required

- **Status:** Verified local configuration correction
- **Issue/cause:** The bot had a 2,048 MB container memory limit and insufficient free memory for the native desktop threshold.
- **Approach and outcome:** Increase Scout to 4,096 MB, preserving its volumes; verify desktop startup and thumbnail. Later native-stack configuration introduced a 1,024 MB default floor when no explicit value exists.
- **Evidence:** [Memory correction](../test-results/desktop-memory-fix.json); [later native configuration](PHASE4-REVIEW.md#october-11-native-tool-access-and-unmodified-hermes).
- **Limit / learning:** Do not conflate increasing one bot’s memory with changing the later runtime default, or promise desktop availability under arbitrary host pressure. Original report: T1-U24, 2026-10-07 08:54:02 UTC (14:24:02 IST).

### Phase 4, before 2026-10-10 cleanup — Connection setup and real provider failures

#### C10 — Slack local OAuth and search-token setup were confusing

- **Status:** Local setup path implemented; provider prerequisites remain
- **Issue/cause:** Slack OAuth could not use the configured local HTTP callback; search required a separate user token rather than only the displayed bot token.
- **Approach and outcome:** Private bot/user token fields, explicit local-HTTP guidance and HTTPS OAuth support when configured.
- **Evidence:** [Phase 4 review](PHASE4-REVIEW.md), P4-12 below.
- **Limit / learning:** Token scope and workspace installation are external prerequisites, not permissions the application can invent.

#### C11 — Slack #test history failed before app membership

- **Status:** Resolved after owner action; error handling improved
- **Issue/cause:** The app was not in the selected channel; a generic error hid not_in_channel.
- **Approach and outcome:** Fixed recovery explanations, owner-added channel membership, then fresh native search/history verification.
- **Evidence:** [Phase 4 review](PHASE4-REVIEW.md); [Google/Slack reads](../test-results/phase4-google-slack-reads.json).

#### C12 — New GitHub account had no repository; selected repo later rejected issue access

- **Status:** External setup/access corrected and reads verified
- **Issue/cause:** Initially there were no repositories. After repository creation, issue access required corrected token scope and reconnect. Empty issue/PR lists were legitimate data.
- **Approach and outcome:** Owner created a test repository and updated access; preserve the failed run and use an explicit fresh read acceptance after reconnect.
- **Evidence:** [Phase 4 report](PHASE4.md); [repository access evidence](../test-results/phase4-github-repository-access.json); [retained failed run](../test-results/phase4-real-reads-failed.json).

#### C13 — Real connection acceptance remained incomplete despite repeated setup updates

- **Status:** Partly verified; remaining private-write gate open
- **Issue/cause:** The chat contains repeated updated/try-now messages during Google, Slack and GitHub setup. A successful read or draft is not proof that a private write completed.
- **Approach and outcome:** Verify exact reads and frozen reviewed writes separately. Calendar creation succeeded; cancelled/unverified Drive, GitHub, Gmail and Slack fixtures were not counted as effects.
- **Evidence:** [Phase 4 report](PHASE4.md); [implementation audit](../test-results/phase4-implementation-audit.json).
- **Limit / learning:** Original turns T1-U39/U40 confirmed repository 404/zero entries; T1-U42 confirmed later Issues/PR permission rejection. T1-U43 verified the fresh combined read run after reconnect. Repeated retry prompts are linked to these distinct setup stages, not separate invented defects.

##### Documented review findings: P4

The following individual findings and corrections are preserved from [PHASE4-REVIEW.md](PHASE4-REVIEW.md). They were discovered during implementation, acceptance or review; they are not all owner-reported production failures. Status is **historically corrected within the source review’s scope**, unless a supersession is noted below. Within this dated stage, the review did not retain exact discovery timestamps; source order is retained rather than inventing them.

| ID | Observed issue / cause | Correction actually recorded | Verification recorded |
|---|---|---|---|
| P4-01 | Generic HTTP clients could follow redirects/proxies or resolve a provider again after admission. | Pin the admitted numeric address, verify TLS against the hostname, refuse redirects and ignore environment proxies. Bound response bytes and interrupt slow headers/body at an absolute deadline. | Actual loopback-server tests cover IP pinning, redirect refusal, error secrecy, response limits, drip deadline and delivery classification. |
| P4-02 | A missing response or lost final database transaction could repeat a completed external action. | Commit dispatch identity before sending. Lost receipts become uncertain, never replay; explicit reconciliation records evidence and resumes without executing. | Provider-loss and post-effect database-loss regressions. |
| P4-03 | A lost transaction could roll back a standing permission's use count after delivery. | Count committed dispatching/uncertain/unresolved/successful operations as well as the counter. | Lost-counter-commit test verifies a second effect cannot spend the same final use. |
| P4-04 | Multiple mutations or unrelated review callbacks could resume a task before its external receipt settled. | Allow one unfinished connector mutation per task; reject overlapping review proposals and fence worker claim while external work is unfinished. | Overlap/worker-gate regressions. |
| P4-05 | Recovery could overwrite a just-completed receipt fetched before it waited for the task lock. | Lock the task, then recheck dispatch status/expiry before marking stale work uncertain. | An actual concurrent stale-recovery/dispatcher test verifies the successful receipt is retained. |
| P4-06 | Connection-policy edits could detach a bot with admitted work. | Check removed as well as newly assigned bots for idle state. | Busy-bot detachment test. |
| P4-07 | An MCP endpoint edit could retain a token and tool policy intended for the old server. | Clear credentials, discovery and permissions; reconnect before use. | Endpoint-change regression. |
| P4-08 | Malicious MCP schemas could resolve remote/self references or run expensive regex/combinator validation. | Reject references/regex validation, bound depth/nodes/size/combinations, distinguish a property named `pattern` from a schema keyword, and validate discovery/page/receipt shapes. | Schema, malformed result, SSE correlation and credential redaction tests. |
| P4-09 | Updating the plugin changed tool count and could break readiness, while counting alone could admit a plugin missing connector tools. | Require the complete named tool set from argument contracts; upgrade all runtimes. | Five healthy compatible runtimes and actual native policy smoke. |
| P4-10 | A connected bot could delegate to an unrestricted helper or lose its task restriction after policy detachment. | Latch the restriction on the root task when any participant has a connection policy; enforce it throughout the task tree, independently of each bot's own connector access. | Delegation/parent/helper and policy-detachment regression; no connector access is inherited. |
| P4-11 | Connected bots could rewrite native plugin/config files using allowed file-writing tools. | Restrict native admission to conservative read/skill/memory tools and authenticated Hermbot operations. Retain the fence after revocation. | Middleware tests block terminal/browser/write/patch/executable/scheduling tools; actual native terminal denial. |
| P4-12 | Slack's HTTP OAuth callback cannot work on the owner's local HTTP URL, and search requires a separate user token. | Expose private bot/user token fields, disable HTTP OAuth with a useful instruction and support HTTPS OAuth when configured. | Token selection/privacy and local-OAuth rejection tests; official Slack docs; responsive form checks. |
| P4-13 | Refresh HTTP 400/missing OAuth configuration did not consistently produce reconnect state. | Classify rejected refresh grants and missing app configuration as authentication recovery; preserve rotated refresh tokens. | OAuth refresh, revoked owner session and rejected-grant tests. |
| P4-14 | OAuth callback request logs could include authorization code/state; validation errors could echo submitted tokens. | Disable API access logs and omit input values from request-validation errors. | Private-input canary regression and deployment configuration. |
| P4-15 | Local revoke did not invalidate provider tokens. | Attempt Google/Slack/OAuth GitHub revocation while always removing local credentials; report provider confirmation, retain manual revocation for imported PAT/MCP tokens. | Provider revocation routing and local fallback tests. |
| P4-16 | GitHub requires a User-Agent, and generated Gmail messages need an authenticated From address. | Set a fixed User-Agent/Accept/version header; use the connected Google account or read its profile, and include From/Date/stable Message-ID. | Fixed-route and MIME/header regressions; real provider runs remain pending. |
| P4-17 | The pinned socket retained its four-second connect timeout for all response reads, despite a longer request budget. | Reset the connected socket to the response budget; the absolute timer still bounds slow responses. | A delayed actual HTTP response succeeds after the short connect budget; public MCP diagnosis exposed the inherited timeout. |
| P4-18 | A public MCP server sent its matching JSON-RPC receipt but kept the SSE stream open, causing a false response-loss timeout. | Return as soon as the matching SSE receipt arrives; keep the existing size/deadline limits and correlation checks. | Live Cloudflare stream diagnosis plus a controlled open-stream transport regression; fresh native proposal/denial, edited approval, provider receipt and continuation passed in `test-results/phase4-mcp-review.json`. |
| P4-19 | Stopping uncertain work allowed owner retry or manager reassignment to submit the same unknown external effect again. | Block both operations across the old task tree until the outcome is verified. A stopped task can close tracking as unresolved without a success/failure claim or replay; unresolved effects still consume admitted standing uses and can be reconciled later. | Real response-loss recovery UI evidence and stop/retry, child reassignment, later reconciliation and standing-budget regressions. |
| P4-20 | Lost application responses displayed as pending owner approval although no approval remained. | Expose application dispatch/recovery state in task projections and a direct Activity link in the work panel. | Task projection regressions and real lost-response browser recovery. |
| P4-21 | The private read acceptance runner counted successful reads without proving all required actions and resources were used. | Correlate native tool-call IDs and normalized argument hashes with committed backend receipts, and require every exact requested read. Reviewed-write verification checks exact approval, provider object ID and native continuation separately; a lost submission can only be recovered by looking up its original key. | Acceptance verifier regressions reject missing actions, wrong resources, mismatched calls, errors, proposals, lost receipts and missing continuations; private runner preflight creates no work without connections. |
| P4-22 | Resource editing removed a comma before the owner could enter another value; closed forms could retain private inputs. | Preserve raw resource input until submit, disable form controls during save and clear private fields on close. Display the server's configured callback. | Browser persisted two resources, private-field clearing and four viewport checks with no script errors. |
| P4-23 | Slack channel history failed with a generic message that hid the actionable `not_in_channel` cause. | Explain known Slack failures with fixed recovery templates, including adding the connected app to the selected channel. Keep arbitrary provider bodies private and retain authentication/uncertainty classification. | Real `#test` failure diagnosis; actual HTTP regressions cover membership, missing scopes, wrong token type, hostile error content and non-Slack spoofing. After owner-added app membership, fresh native search/history and cross-bot report handoffs passed. |
| P4-24 | Automatic resource resolution could choose an unrelated or ambiguous private file/channel. | Require exactly one matching H200 benchmarks CSV and the owner's exact channel name before import/history. Record original submission keys and never resubmit to recover a lost response. | Three resource-selection regressions; real native discovery resolved `H200 benchmarks (1).csv` and `#test`. |
| P4-25 | The native review schema omitted `connector`, so Quill substituted a generic review for a Slack post authorization. | Advertise every backend review kind in the native tool; explain connector payloads; reject connector-shaped generic reviews on the server. Verify exact typed authorization during private review preparation. Cancel the invalid fixture, upgrade all five runtimes without removing private volumes, and prepare fresh drafts. | Native tool-call/hash/receipt correlation proves both replacement Gmail/Slack drafts use kind `connector` and frozen exact payloads. `test-results/phase4-native-review-schema.json`, 216 passing backend tests, and aligned-kind/generic-rejection/false-preparation regressions. No private provider effect has been claimed. |
| P4-26 | The full private read runner could not recover acceptance after a token-scope failure without manually replacing its original manifest. | Record verification failures, verify the original independently of live reconnect status, and provide explicit read-only retry that archives prior evidence only after the exact original tree/executions have completed with zero external proposals. | Eight retry-integrity regressions; 216 passing backend tests. Actual failed GitHub issue receipt retained, original verification creates no task, and retry refused before submission while GitHub needed reconnect, then preserved the original failed run and passed fresh native three-provider reads after scope correction. |



**Supersession:** P4-10/P4-11 record the original connection-triggered native-tool restrictions. That design was later removed at the owner’s direction (C17/NATIVE-01). P4 review status does not close the full private-write acceptance gate.

### 2026-10-10 — Test-fixture clutter, group removal and work-list behavior

#### C14 — Accumulated test groups and two UI bots cluttered actual usage

- **Status:** Owner-authorized cleanup verified
- **Issue/cause:** Acceptance fixtures remained visible in the working roster.
- **Approach and outcome:** Archive/remove 59 fixture groups while retaining saved records/outputs; delete the two fixture bots through lifecycle operations, preserving original bots/volumes and the already-created calendar effect. Add group removal with settled-work checks.
- **Evidence:** [Cleanup receipt](../test-results/test-fixture-cleanup.json); [group-removal UI](../test-results/group-cleanup-ui.json).
- **Limit / learning:** The later how-to-delete-a-group question refers to this existing feature, not evidence of a second deletion defect.

#### C14A — Kaggle competitions: five failures discovered in actual usage

Original inspection: T1-U47/U48, 2026-10-10 08:25–08:29 UTC. The earlier C16A placeholder did not preserve these individual failures; the original transcript now supplies them.

| ID | Issue and diagnosed cause | Approach, evidence and current limit |
|---|---|---|
| KAG-01 | Public Kaggle research was blocked because Scout's Google connection triggered a native-tool fence across the collaborating tree. | C17 removed this design and verified actual connected native browsing. The old owner request was not rerun merely to deploy the fix. |
| KAG-02 | Scout sent plain words to `drive.search.query`, which is forwarded as Google's Drive filter `q`; it returned HTTP 400. The schema did not explain that grammar. | **Open interface/guidance gap.** Current [catalog](../backend/connector_catalog.py) still declares generic `query`, and [adapter](../backend/connector_providers.py) forwards it directly. A plain-text search wrapper was proposed, but that proposal is not a shipped fix. |
| KAG-03 | Sage/Scout loaded leftover enabled test skills, including `hermbot-phase3-orchestrate` and an unrelated CSV report procedure. Cleanup had removed test groups/bots while missing enabled skills. | **Cleanup closure unverified; last saved snapshots still show them enabled.** Automatic relevance selection was implemented in SKILL-02, but [skills acceptance](../test-results/skills-tools-native.json) restored the original test-skill selections; [coordination snapshot](../test-results/coordination-before.json) still contains them. Do not claim they were removed, or alter owner configuration as part of this documentation task. |
| KAG-04 | Scout supplied an unverified competition shortlist and generalized medal thresholds; Sage repeated the unsupported guidance. The original diagnosis called that guidance misleading. | Quality review/evaluation and completion tracking were later strengthened (C29/C32), but **the factual correction of this original answer and successful rerun are not verified**. The record preserves the historical finding without introducing new claims about today's Kaggle rules. |
| KAG-05 | Tasks were marked completed even though the requested shortlist was unverified; two local searches each took about 120 seconds. | Completion and coordination changes address distinct mechanisms (C19/C29/C32). The later lock cycle was reproduced and fixed in C31, but these earlier search durations alone do not prove the same root cause. No independent historical trace confirms every early search delay resolved. |

#### C15 — Every ordinary user message became a work item

- **Status:** Implemented and historically verified; later interpretation defects remain
- **Issue/cause:** Ordinary messages used the task endpoint and the sidebar listed root executions, conflating discussion, status, changes and assignments.
- **Approach and outcome:** Separate work items from executions, automatically route ordinary messages and associate updates with existing work; retain visible failures without manufacturing assignments.
- **Evidence:** [UI review](UI-REVIEW.md); [work-item history](../test-results/work-items-history.json); [native acceptance](../test-results/work-items-native.json).
- **Limit / learning:** Initial model-side routing was later replaced by the application intake/controller in C28. Current M findings show remaining semantic gaps.

#### C16 — Manual context-selection dropdown increased user effort

- **Status:** Removed and verified
- **Issue/cause:** The first interaction design exposed context/message classification choices to the user.
- **Approach and outcome:** Remove the message-intent dropdown, work selector and inline classification controls; infer intent/context automatically and clarify only actual ambiguity.
- **Evidence:** [UI review](UI-REVIEW.md); [automatic-only browser acceptance](../test-results/work-items-ui.json).

### 2026-10-10 — Skills, tools and collaboration context

#### C16A — Owner-reported failures in the latest group prompted skills/tools redesign

- **Status:** Original diagnosis recovered; implementation scope verified, KAG-02/KAG-03/KAG-04 remain separately limited.
- **Issue/cause:** The group was **Kaggle competitions**. T1-U47/U48 identified the five KAG findings above; T1-U49 requested the broader skill/tool redesign, which paused for C15 and was implemented following T1-U56.
- **Approach and outcome:** Separate installed/enabled/relevant skills, expose actual capabilities and carry explicit handoff inputs. Preserve each diagnosed problem rather than treating the whole group as fixed by this upgrade.
- **Evidence:** Original transcript T1-U47/U48/U49/U56; [skills/tools plan](SKILLS-TOOLS.md); [review](SKILLS-TOOLS-REVIEW.md).
- **Limit / learning:** The original snapshots restored test skills and the Drive query remains a raw filter. Those are omissions from the original proposed cleanup/search approach, not resolved by relevance selection.

##### Documented review findings: SKILL

The following individual findings and corrections are preserved from [SKILLS-TOOLS-REVIEW.md](SKILLS-TOOLS-REVIEW.md). They were discovered during implementation, acceptance or review; they are not all owner-reported production failures. Status is **historically corrected within the source review’s scope**, unless a supersession is noted below. Within this dated stage, the review did not retain exact discovery timestamps; source order is retained rather than inventing them.

| ID | Observed issue / cause | Correction actually recorded | Verification recorded |
|---|---|---|---|
| SKILL-01 | Configured native tools remained advertised after connection policy blocked them. Rejected calls were the model's first useful indication of missing access. | A shared effective inventory now projects workspace/bot permissions, probed native availability, conversation intent, human control and connection policy into both request schemas and execution admission. Preparation checks explicit tool/application requirements before work. | Capability, schema and plugin regressions; actual request visibility contains no forbidden terminal/browser/write tools. |
| SKILL-02 | Installed, enabled and relevant skills were conflated, and a manual composer picker increased owner effort. | The responding model selects up to three relevant enabled procedures from authenticated descriptions. Empty selection is valid. The composer picker is removed; enabling remains an owner setting. Required tools are validated without enabling anything. | Selected/irrelevant/disabled/missing skill regressions; actual relevant brief procedure selected, unrelated invoice skipped, browser has no skill picker. |
| SKILL-03 | Pinned native `skill_view` ignored a model `preprocess` argument and could execute inline shell/template examples. | Raw adapter consultation and a guarded native wrapper patch disable preprocessing for Hermbot bots. Procedure bodies are returned privately to the current task, not in capability discovery or public preparation events. | Raw consultation regression, successful pinned-source guard and real native wrapper preserving literal inline text without creating its marker file. |
| SKILL-04 | Discovery of a connected peer could permanently tighten a task's native-tool fence before that peer participated. | Peer capability preview uses `latch=False`; actual participation still tightens the root fence. Discovery cannot change execution authority. | Separate discovery and actual connected-handoff regressions. |
| SKILL-05 | A previously successful preparation could survive continuation, credential revision or a newly restricted task tree. | Preparation records the actual attempt and a fingerprint of current capability facts, including connection revision. Tool admission recomputes it; immutable old receipts return their historical result without readmitting execution. | Continuation, connection revision, resource scope and delegation regressions. |
| SKILL-06 | A native run ending normally could report logical success despite missing access or no current preparation. | Settlement fails blocked or unprepared work while retaining the bot's explanation in the transcript. Permission changes before completion also invalidate preparation. Existing child/review/human-control continuations retain precedence. | Blocked, absent-preparation, changed-policy and successful-alternative settlement regressions. |
| SKILL-07 | Handoffs could describe a file without granting or staging the owner attachment. Private inputs risked being confused with shared context. | Explicit attachment IDs must be a subset of the caller's admitted inputs and belong to its conversation. Handoffs persist a bounded public objective and expected output, stage the exact granted inputs, and preserve existing artifact access/root/grant boundaries. | Granted/ungranted attachment and staging regressions; actual recipient read result and both report downloads contain the supplied progress, owner and date. |
| SKILL-08 | Missing recipient requirements could consume a handoff budget or create an unusable child. | Validate explicit recipient skill requirements before child creation or budget charge. Return typed blockers through an immutable receipt; reassignment retains inputs and does not rewire dependencies on a blocked replacement. | Missing recipient requirement/idempotency regression and reassignment review. |
| SKILL-09 | New optional handoff arguments changed the hash of historical tool calls even when absent. | Strip only empty new fields before computing delegation/message receipt identity. Existing argument identity is preserved; nonempty shared inputs remain part of the immutable hash. | Historical empty-field receipt replay regression and cumulative receipt regressions. |
| SKILL-10 | The first real run exposed that a `pre_llm_call` hook's ContextVar writes stay in its copied timeout-worker context and do not reach request assembly. Every tool was consequently hidden. | Replace hook-local binding with an additive generic `tool_schemas` middleware surface carrying stable framework identities directly from the assembling agent. The registered plugin returns the filtered request copy. Also fail an unrouted response instead of reporting success when tools never became available. | Framework identity, absent/failed filter and unrouted-settlement regressions; all four real cases pass after the correction. |
| SKILL-11 | The first cumulative test run closed the shared test pool through a new test's application lifespan. | Keep the owner-route test client outside application lifespan and close only the client. | The corrected cumulative suite passes. |
| SKILL-12 | A saved native-history request during skill refresh raised an internal server error while the runtime restarted. | Return a static retryable availability explanation with `Retry-After`, distinguish absent native history, and retain the saved session identity. No upstream private error body is exposed. | Offline/auth/missing-history regressions all recover the same saved session after the dependency returns. |



**Supersession:** connection-policy fences in SKILL-01/SKILL-04/SKILL-05 and source-patch mechanisms in SKILL-03/SKILL-10 describe the October 10 build. The October 11 implementation replaced the patches and removed connection-triggered blocking. Fingerprint freshness and authorized tool/schema consistency remain relevant independently.

### 2026-10-11 — Web access and the unmodified-harness requirement

#### C17 — Connected bots could not use the internet/native tools during tasks

- **Status:** Owner-requested design correction implemented and verified
- **Issue/cause:** Connection policy blocked browser, terminal and writes, including connected task-tree helpers; this prevented legitimate research alongside connectors. The original design tried to prevent native actions bypassing connector controls.
- **Approach and outcome:** Remove connection-based native restrictions; keep connector credentials and connector authorization in the backend. Admit enabled native tools under normal task/human-control rules.
- **Evidence:** [October 11 review](PHASE4-REVIEW.md#october-11-native-tool-access-and-unmodified-hermes); [native acceptance](../test-results/native-tools-acceptance.json).
- **Limit / learning:** Independently authenticated native actions remain outside connector-review coverage. Removing the blacklist did not make those actions backend-reviewed.

#### C18 — The implementation had changed underlying Hermes source

- **Status:** Corrected; historical source patches removed
- **Issue/cause:** Four compatibility/integration patches violated the desired application-above-harness boundary.
- **Approach and outcome:** Remove patch installation; use supported llm_request/tool_execution middleware, configuration and the private adapter. Verify upstream source equality and protect managed code.
- **Evidence:** [Source comparison](../test-results/native-tools-source.json); [October 11 review](PHASE4-REVIEW.md#october-11-native-tool-access-and-unmodified-hermes).
- **Limit / learning:** The owner later deferred the broader harness-independent platform; keeping Hermes unmodified remains mandatory. Alternative harnesses were not implemented.

##### Documented review findings: NATIVE

The following individual findings and corrections are preserved from [PHASE4-REVIEW.md](PHASE4-REVIEW.md). They were discovered during implementation, acceptance or review; they are not all owner-reported production failures. Status is **historically corrected within the source review’s scope**, unless a supersession is noted below. Within this dated stage, the review did not retain exact discovery timestamps; source order is retained rather than inventing them.

| ID | Observed issue / cause | Correction actually recorded | Verification recorded |
|---|---|---|---|
| NATIVE-01 | Connection policy banned enabled terminal, browser, file editing and other native tools, including collaborating bots. | Remove the connection restriction from capability discovery and execution; ignore old root-task latches without rewriting historical records. | Connected/revoked/delegated capability and execution regressions; actual connected bots execute native tools. |
| NATIVE-02 | Four custom patches changed upstream Hermes source. | Remove the patch installer. Use existing `llm_request` middleware for request projection, existing `tool_execution` middleware for raw skill consultation, and the private adapter for the owner skill catalog. | Compare all 2,169 upstream Python files in the official pinned image, built image and three running runtimes. |
| NATIVE-03 | Native tools could replace runtime code or the managed extension. | Read-only root filesystem and a root-owned plugin mount; drop to the Hermes user, drop effective capabilities and forbid privilege escalation. Keep each bot's state/workspace/browser volumes writable. | Kernel/filesystem probes verify read-only source, immutable managed plugin, non-root gateway, zero effective capabilities, writable workspace and absence of a Docker socket. |
| NATIVE-04 | The new read-only root broke attachment staging into the code directory; Docker archive uploads also refused the replacement tmpfs path. | Stage bounded chunks through server-controlled Docker Exec into a root-only `/run/hermbot-staging` directory, then invoke the unchanged private attachment installer. Reject unsafe directory paths and keep payloads inaccessible to the bot. | Chunk reconstruction, unsafe staging path and existing installer symlink regressions; fresh native child artifact transfer and parent continuation pass. |
| NATIVE-05 | The official image inherited a write-safe root excluding the private workspace; the default Browser Use CLI did not match the advertised built-in browser tools, and headed browsing had no display. | Configure all private writable roots, select the native built-in browser stack and enable lazy desktop startup. Use a 1,024 MB desktop memory floor unless an explicit existing setting is present. | Actual native file creation, patching, terminal read/arithmetic and `browser_navigate` succeed; the real browser returns `Example Domain`. |
| NATIVE-06 | A model can finish with an honest partial report even when required native checks failed. | Verify actual successful tool receipts, not task completion alone; decode Hermes's untrusted browser-result wrapper and exclude historical failed attempts. Retain failures before explicit fresh local recovery. | The first partial file/browser run is rejected, retained in `review_failures`, and followed by a successfully verified fresh run. |



### 2026-10-11 — Slack DM, ordinary answers, result tracking and handoffs

#### C19 — Slack DM workflow and ordinary information requests could not complete cleanly

- **Status:** Implemented and verified within the outcome/handoff acceptance scope
- **Issue/cause:** Recipient targeting, ordinary answers versus approvals, required results and helper responsibility lacked a sufficiently explicit contract. Original T1-U76 diagnosed three specific failures: bots incorrectly required a DM channel despite a confirmed member ID; an ordinary input used an approval action returning `once`; and the work appeared completed without delivery. No send or send-approval proposal existed then.
- **Approach and outcome:** Support confirmed member-ID DM targeting, durable typed questions and outcome-bound dispatch/receipts. Do not require history readback to prove a successful send.
- **Evidence:** [Work outcomes review](WORK-OUTCOMES-REVIEW.md); [acceptance](../test-results/work-outcomes-acceptance.json).
- **Limit / learning:** Synthetic draft preparation is not a live private send. Later M01/M11/M14/M15/M17 expose additional mechanisms, not proof this historical implementation never worked.

##### Documented review findings: OUT

The following individual findings and corrections are preserved from [WORK-OUTCOMES-REVIEW.md](WORK-OUTCOMES-REVIEW.md). They were discovered during implementation, acceptance or review; they are not all owner-reported production failures. Status is **historically corrected within the source review’s scope**, unless a supersession is noted below. Within this dated stage, the review did not retain exact discovery timestamps; source order is retained rather than inventing them.

| ID | Observed issue / cause | Correction actually recorded | Verification recorded |
|---|---|---|---|



#### C20 — Kaggle helper could not prepare Slack delivery; bots asked owner to repair internal IDs

- **Status:** Follow-up correction implemented; real handoff/review verified
- **Issue/cause:** Sage prepared with no results and delegated without outcome IDs; Quill could discover the DM route but had no assigned connector result. Bots incorrectly said the schema lacked outcomes. Existing tests exercised valid fields without enforcing their use. Empty settled contracts stayed planning.
- **Approach and outcome:** Require owner-facing result registration and relevant handoff outcome IDs before child creation; preserve authority; expose needs_attention for empty settled contracts and accurate repair guidance.
- **Evidence:** [Follow-up diagnosis and four regressions](WORK-OUTCOMES-REVIEW.md#follow-up-missing-result-registration-and-handoff); [real handoff proof](../test-results/outcome-handoff-acceptance.json).
- **Limit / learning:** The exact test draft was denied without dispatch. This fix did not send the historical owner request.

### After outcome work, before execution redesign — Response controls and stale completion warnings

#### C21 — Inspect response and Stop response had awkward positioning

- **Status:** Responsive layout checks verified
- **Issue/cause:** The owner reported response-action placement problems. Original response T1-U83 confirms the fix aligned controls below the relevant bot response text and allowed wrapping on small screens; the acceptance verifies correct action targets. The transcript does not establish a more specific selector-level root cause.
- **Approach and outcome:** Correct response-action presentation; verify streaming, queued and failed states plus correct inspect/stop targets at five widths.
- **Evidence:** [Response-layout acceptance](../test-results/response-layout-check.json).
- **Limit / learning:** Reported 2026-10-11 08:35:20 UTC; fix reported 08:39:46 UTC (T1-U83). Do not invent a precise selector-level cause.

#### C22 — Completed Slack work still showed Needs access and Retry task

- **Status:** Corrected and verified
- **Issue/cause:** Original T1-U87/U88 confirms an optional Slack history read failed after a successful send. Hermbot incorrectly treated that read permission failure as a problem with the whole connection, while the card also presented the execution failure as unfinished delivery.
- **Approach and outcome:** Keep outcome completion authoritative; preserve the execution failure as a note, show receipt, remove Retry and invalid skill actions for completed work.
- **Evidence:** [Completion correction](../test-results/slack-completion-fix.json).
- **Limit / learning:** 314 recorded backend tests; no new Slack message was sent by the fix. The historical failed execution was preserved rather than rewritten.

#### C23 — Quill's Slack retry failed because both tokens were rejected

- **Status:** Authentication failure identified; later connected/send recovery observed. Error classification and connection-wide permission handling were corrected in the follow-up.
- **Issue/cause:** Original T1-U85 at 2026-10-11 08:44:04 UTC was followed by a diagnosis at 08:47:00 UTC: the bot token returned `account_inactive`, the user/search token returned `token_revoked`. This was distinct from a missing DM ID or mistyped connection ID. A generic scope/resource/credential message hid these actual causes.
- **Approach and outcome:** Mark reconnect needed for genuinely inactive/revoked authentication; provide private reauthorization/token replacement guidance. Later T1-U87/U88 and [completion proof](../test-results/slack-completion-fix.json) confirm successful posting and a connected account. Treat optional `missing_scope`/wrong-token action restrictions separately so they do not disable permitted actions.
- **Evidence:** Original T1-U84/U85/U86/U87/U88; [Slack recovery classification](../backend/connector_http.py); [completion correction](../test-results/slack-completion-fix.json).
- **Limit / learning:** A subsequent successful send supports recovered posting; it is not independent proof that every old token or search scope was replaced and reverified. T1-U84 was immediately clarified by U85; T1-U81 was similarly clarified by U82 and belongs to C20. These are no longer unknown reports.

### 2026-10-11–12 — Starlink research cutoff, remaining obstacles and monitoring

#### C24 — Scout did not complete a long request; five-minute execution cutoff

- **Status:** Continuous execution correction verified
- **Issue/cause:** Original T1-U89 inspected Scout’s **Starlink** request: after 300 seconds Hermbot cancelled it with “Execution time budget exhausted,” and the browser reported an explicit stop even though the owner had not clicked Stop. A Markdown draft existed; no Word file or Slack delivery existed. This was independent of the later 24-turn cap (TEMP-05).
- **Approach and outcome:** Remove automatic five-minute cutoff/default twenty-minute deadline; use continuous native runs, owner controls, explicit waits and active-work budgets. No artificial checkpoint/yield loop was adopted.
- **Evidence:** [Continuous native proof](../test-results/continuous-execution-live.json); [execution behavior](../README.md).
- **Limit / learning:** Proof ran a 320-second native command, about 337 active seconds, one attempt, and survived worker restart. It does not guarantee arbitrary research completes or eliminate explicit budgets/deadlines.

##### Remaining obstacles in the original Starlink request

Original diagnosis: T1-U89, 2026-10-11 12:34:26 UTC. Removing the timeout alone did not establish that this request could finish.

| ID | Issue / cause | Status, approach and evidence |
|---|---|---|
| STAR-01 | Scout checked for `python-docx` and the package was missing, leaving Word generation unready. | **Historical dependency gap; specific package correction unverified.** Direct file publication/transfer later shipped (TEMP-06), but that is not proof the package was installed or this file generated. No package install is performed by this backfill. |
| STAR-02 | Scout could see its Google account but lacked its own Slack connection and made no handoff to Quill, which had delivery access. | **Observed planning/handoff failure; original delivery unverified.** Capability discovery and outcome-bound handoffs later gained acceptance (C20/C32). This does not prove the old Starlink request was completed or authorize sending it now. |

#### C25 — Progress versus stuck execution was hard to determine

- **Status:** Monitoring implemented; later activity/UI corrections in C30
- **Issue/cause:** Runtime contact alone does not establish useful progress; repeated tool failures and unexplained inactivity need separate evidence.
- **Approach and outcome:** Optional asynchronous execution assessment and visible budget/assessment details. Assessments may guide recovery but cannot approve actions or declare completion.
- **Evidence:** [Monitoring UI proof](../test-results/execution-monitoring-ui.json); [execution monitor](../backend/execution_monitor.py).
- **Limit / learning:** Decisions configuration applies to execution monitoring; intake remains a separate path. M24/M25 are not solved by enabling monitoring.

#### C26 — Decisions access was conflated with API/subscription configuration

- **Status:** Clarified; subscription integration deferred
- **Issue/cause:** The user asked whether Codex subscription or an existing OpenAI-compatible API configuration automatically enabled Decisions. These are separate capabilities/configurations.
- **Approach and outcome:** Keep explicit provider/model/mode settings; record native Decisions versus structured-chat fallback accurately and expose assessment state.
- **Evidence:** [Local adapter](../backend/decisions.py); [configuration documentation](../README.md).
- **Limit / learning:** No claim of universal provider or subscription entitlement; no subscription login adapter was implemented. This entry records an integration boundary, not a proved provider outage.

### 2026-10-12–13 — Project chat temp2 and general work revision/controller changes

#### C27 — Project chat temp2 exposed broader revision, coordination and result-quality problems

- **Status:** General application change implemented; current gaps remain
- **Issue/cause:** Original T1-U102 reviewed 20 messages, 17 tasks and tool records; T1-U104 revisited the design causes. The individual findings are recovered in TEMP-01–TEMP-11 below, including a mistyped connection ID, rejected owner corrections, a pending Redirect, the 24-turn cap, DOCX delivery gaps and obsolete requirements.
- **Approach and outcome:** Move owner intent application and versioned requirements into the application; maintain pending owner directions, current authorization, shared result bindings and execution reconciliation across bots.
- **Evidence:** [Controller review](../test-results/work-controller-review.json); [native acceptance](../test-results/work-controller-native.json).
- **Limit / learning:** The document-format change is an acceptance scenario, not a task-specific architecture. C27A records the owner’s correction of that framing. Current tests and implementation support the general corrections; the old external actions were not replayed.

##### Recovered individual Project chat temp2 findings

Original diagnosis: T1-U102, 2026-10-12 05:09:20 UTC; design re-audit T1-U104, 13:24:23 UTC. The research memo and Google Doc existed, but no Slack DM had been sent. The following are individual findings, not a blanket claim that the group was rerun successfully.

| ID | Observed failure and root cause | Implemented approach / evidence / limit |
|---|---|---|
| TEMP-01 | Quill discovered the correct connection ending `c4fddd0f4352` but submitted a nonexistent ID ending `c4fddd1c4352`. Preparation diagnosed missing access instead of invalid reference. | **Application classification corrected.** Current [capability checks](../backend/capabilities.py) separate `connection_reference_invalid` from `connection_access_missing`; name/validated-reference resolution was added. A model can still supply a bad ID (M18/M23). |
| TEMP-02 | Retries rediscovered the real connection but repeated the bad ID; Sage copied it into assignments. Permission changes could not fix an identifier error. | **Recovery interface improved; universal model recovery unproven.** Exact discovery/named references and differentiated errors reduce the burden. Preserve this failure separately from revoked authentication in C23; no historic send is replayed. |
| TEMP-03 | “Create a google doc ... do it” and “@scout follow that” attempted owner updates through Scout; ownership/grant checks rejected them, and they became discussion-only. | **Cross-bot owner updates implemented and verified.** [Controller native proof](../test-results/work-controller-native.json) verifies owner revision through another teammate without transferring management credentials. Later intake failures remain distinct and open. |
| TEMP-04 | Redirect stayed pending while the manager waited on an upload whose prerequisite had been cancelled. Storing the correction did not wake/reconcile waiting work. | **Durable revision commands and dependency recovery implemented.** [Controller review](../test-results/work-controller-review.json) includes settlement-race correction and waiting-manager checks. Historical cancelled operations remain preserved. |
| TEMP-05 | Scout and Quill hit `max_iterations_reached(24/24)` despite useful outputs. Removing the five-minute cutoff had left a separate bootstrap cap. | **Supported configuration corrected and verified beyond 24 turns.** [Bootstrap](../docker/hermes_bootstrap.py) uses a large positive ceiling governed practically by active budget; [native proof](../test-results/work-controller-native.json) records 30 model/tool turns. This closes the specific cap omission, not all long-task limits. |
| TEMP-06 | Quill generated/validated DOCX, but text-only model publication and text-based Drive creation could not transfer/upload the actual file. Isolation was complete while shared-file transport was incomplete. | **Direct file publication/transfer and `drive.upload` implemented.** [Artifact path](../backend/artifacts.py), [catalog](../backend/connector_catalog.py), and native shared-file proof support actual binary files. Real provider-write acceptance was not performed in this rollout; ZIP support remains missing (M04). |
| TEMP-07 | Accepting Google Docs did not supersede `word_report`; the obsolete required result kept work at 2 of 4. | **Versioned requirement supersession implemented/evaluated.** [Live intake cases](../test-results/work-intake-live.json) cover format replacement preserving research/delivery. M10 and M06 show distinct remaining lifecycle/derivation gaps. |
| TEMP-08 | Scout could not reliably explain Sage’s work during group status questions. Task-tree/private-state restrictions were not matched by sufficient shared public-work visibility. | **Shared work-status interface implemented.** [Status tool](../backend/service.py), controller/native/UI checks expose scoped assignments, results and blockers while leaving private memory/files separate. C30 later corrects conflicting UI projection. |
| TEMP-09 | “Don’t ask for approvals” remained prompt text, so Google Doc creation still required formal exact review; requested action, account access and review policy were conflated. | **Explicit request authorization and configurable action policy implemented.** [Controller review](../test-results/work-controller-review.json) verifies scopes/policy before dispatch using mocked effects. Broad conversational preference is not blanket standing permission; policy still governs. |
| TEMP-10 | Memo reported 23 sources, 21 full-text skims and two abstract/bibliographic checks; Quill relied on Scout’s synopsis instead of independently reading papers. | **Research-depth limitation remains.** C29’s controlled quality evaluation and C32’s accountable review do not prove exhaustive reading or universal factual accuracy. Do not call source counts or multiple agents independent verification. |
| TEMP-11 | Application appended “this answer does not approve any external change”; Sage then attributed that policy statement to the owner, who had requested Google Docs and Slack. | **Provenance wording corrected.** [Answer continuation](../backend/inputs.py) separates application policy from the actual authenticated answer; M14/M27 show remaining contextual-provenance gaps. Exact task authorization is recorded separately from commentary. |

#### C27A — Implementation language made a systemic change sound like a one-task patch

- **Status:** Communication corrected; systemic scope retained.
- **Issue/cause:** The phrase “first implementation slice” focused on DOCX-to-Google-Doc replacement. The owner correctly challenged the framing (T1-U107/U108/U109).
- **Approach and outcome:** Rephrase architecture, implementation steps and regression scenarios distinctly. Build shared work revisions, recovery, authorization and artifact interfaces; retain the particular document change as one test case.
- **Evidence:** Original T1-U108/U109/U110, 2026-10-12 18:10–18:14 UTC; controller implementation and review follow T1-U111.

#### C28 — Automatic interpretation did not robustly represent discussion, updates and new actions

- **Status:** Application intake/controller implemented and evaluated; M01/M10–M17/M24–M27 remain open
- **Issue/cause:** Routing and execution were too closely coupled; changes, mixed messages and independent objectives needed explicit handling above the harness.
- **Approach and outcome:** Interpret owner messages into validated operations and current work revisions, allow scoped earlier-context lookup, preserve original provenance and apply changes transactionally.
- **Evidence:** [24 live interpretation cases](../test-results/work-intake-live.json); [controller review](../test-results/work-controller-review.json).
- **Limit / learning:** Finite successful interpretation cases do not establish universal message understanding. Later Manik failures are retained instead of counting the earlier acceptance as permanent resolution.

#### C29 — Research could be called complete without adequate inspected source evidence

- **Status:** Controlled quality evaluation added; not a complete general solution
- **Issue/cause:** Citations or plausible summaries alone do not establish source inspection or supported findings.
- **Approach and outcome:** Evaluate grounded comparison, citation-without-full-text, unsupported findings and claimed review without inspection; later add accountable contribution review (C32).
- **Evidence:** [Quality evaluation](../test-results/result-quality-live.json).
- **Limit / learning:** These four controlled assessments are not a production guarantee of factual correctness or scope satisfaction; M03/M05 remain open.

##### Controller review corrections, recorded 2026-10-12 20:41 UTC (2026-10-13 in the owner’s timezone)

Each correction below is recorded in [work-controller-review.json](../test-results/work-controller-review.json). Verification for this build: 398 backend tests, 24 live intake cases, four controlled quality cases, 30 native model/tool turns, cross-bot revision and actual artifact transfer, responsive UI checks. Connector effects used mocks; no live external write acceptance is inferred.

| ID | Recorded correction / addressed failure |
|---|---|
| CTRL-01 | Persist pending owner controls in the requirement-revision transaction so run settlement observes them. |
| CTRL-02 | Merge multiple updates to the same work item from one owner message. |
| CTRL-03 | Preserve unchanged targets, dependencies, optional results, coordinator selections and coordination grants. |
| CTRL-04 | Publish intermediate files as supporting evidence without claiming external delivery. |
| CTRL-05 | Reject inconsistent or duplicate delivery conversions while permitting superseded requirements and distinct destinations. |
| CTRL-06 | Recheck owner request scopes, current permissions and review policy before connector dispatch. |



### 2026-10-13 IST (reports began 2026-10-12 UTC) — Conflicting status, slow tools and coordination completion

#### C30 — Sidebar said queued while work said working; activity looked stuck

- **Status:** UI and progress evidence correction verified
- **Issue/cause:** Different projections emphasized queued assignments versus an active execution; runtime contact was not equivalent to observed tool progress.
- **Approach and outcome:** Prioritize active execution and show queued assignments separately; expose actual observed activity and event timestamps.
- **Evidence:** [Tool-gate review/UI verification](../test-results/tool-gate-review.json); [UI proof](../test-results/tool-admission-ui.json).
- **Limit / learning:** This correction makes status more accurate; it cannot establish that every long operation is making useful progress.

##### Additional retrieval obstacles observed during the slow research run

Original T1-U114 diagnosis, 2026-10-12 20:58:42 UTC (2026-10-13 02:28:42 IST):

| ID | Observation | Classification / response |
|---|---|---|
| RET-01 | One PDF extraction required OCR; Sage recovered by reading extracted text. | **Source-processing constraint with observed recovery**, not evidence the lock fix repaired every PDF workflow. No general OCR support guarantee or comprehensive document-quality acceptance is recorded. |
| RET-02 | A Crossref lookup returned `429 Too Many Requests`. | **External rate limit observed.** It contributed delay alongside internal lock/coordination problems. No saved evidence proves a general automatic backoff/rate-limit solution was implemented; do not mark it fixed merely because later research progressed. |

#### C31 — Simple tools repeatedly took 120–240 seconds

- **Status:** Root cause reproduced and correction verified; same issue as H01
- **Issue/cause:** Exclusive Hermbot admission could hold a lock inside a later callback waiting for an earlier Hermes start-order entry that was itself waiting on that lock.
- **Approach and outcome:** Shared per-callback admission plus exclusive takeover drain; preserve epoch, stop and exception semantics without changing Hermes.
- **Evidence:** [Detailed H01](ISSUES.md#h01--tool-admissionstart-order-circular-wait); [diagnosis](../test-results/tool-lock-diagnosis.json); [verified correction](../test-results/tool-gate-review.json).
- **Limit / learning:** Single-tool takeover tests had missed reversed parallel entry. The later correction verifies both concurrency and takeover, not just reduced tool latency.

#### C32 — Quill coordination messages blocked work; research completed prematurely

- **Status:** Coordination correction verified; same issue as H02, semantic acceptance gaps still open
- **Issue/cause:** Informational messages used blocking assignment semantics; publication/contribution completion was conflated with acceptance of the shared deliverable.
- **Approach and outcome:** Nonblocking inboxes, explicit required waits, contribution versus deliverable responsibility, accountable review and inspected-artifact evidence.
- **Evidence:** [Detailed H02](ISSUES.md#h02--informational-messages-blocked-work-helper-output-could-complete-shared-work); [coordination review](../test-results/coordination-review.json).
- **Limit / learning:** 445 recorded backend tests and real three-bot acceptance; M03/M05/M06 later show that ownership and inspection checks alone are insufficient.

##### Review corrections accompanying the concurrency and coordination fixes

These are the actual recorded review fixes, not new implementation in this backfill. All share their parent change’s date; individual discovery order is not known.

| ID | Recorded correction | Evidence |
|---|---|---|
| GATE-01 | Callback OSError must propagate; do not reinterpret an error after execution as an unexecuted admission failure. | [tool-gate-review](../test-results/tool-gate-review.json) |
| GATE-02 | A changed epoch for the same viewer must invalidate a pending takeover. | [tool-gate-review](../test-results/tool-gate-review.json) |
| GATE-03 | Each callback uses its own non-inherited descriptor and rejects unsafe lock files. | [tool-gate-review](../test-results/tool-gate-review.json) |
| GATE-04 | Tests force reversed entry and drain across two callbacks; asynchronous scheduling is not inferred from sequential success. | [tool-gate-review](../test-results/tool-gate-review.json) |
| COORD-01 | Responses requests preserve their native input/instructions format; Chat Completions retains messages | [coordination-review](../test-results/coordination-review.json) |
| COORD-02 | Successful provider receipts survive pending acceptance | [coordination-review](../test-results/coordination-review.json) |
| COORD-03 | Owner revisions refresh active contribution bindings | [coordination-review](../test-results/coordination-review.json) |
| COORD-04 | Delivered but unanswered messages recover after execution failure without adding a blocking child | [coordination-review](../test-results/coordination-review.json) |
| COORD-05 | Archived conversations cannot trigger new peer executions | [coordination-review](../test-results/coordination-review.json) |
| COORD-06 | Replacement preserves required status and rejects running or uncertain work | [coordination-review](../test-results/coordination-review.json) |
| COORD-07 | Acceptance requires actual artifact inspection receipts and excludes stale revisions | [coordination-review](../test-results/coordination-review.json) |



### 2026-10-13 — Manik’s task, ordered by incident progression

All 28 detailed findings remain in [ISSUES.md](ISSUES.md). The ordering below follows earliest identifiable incident occurrence or message; overlapping tool calls and causes spanning several messages do not have a falsely precise total ordering. Most were diagnosed later during the full audit. No M-series defect was fixed by that audit or documentation backfill. Correctly enforced guards and external constraints are distinguished in the detailed record.

| Sequence | Issue | Earliest identifiable evidence / chronology | Current status |
|---|---|---|---|
| 01 | [M28: Chat secret propagated into task/attempt records](ISSUES.md#m28--chat-credential-copied-into-execution-records) | Initial owner submissions, messages 544–545 | Open |
| 02 | [M04: ZIP publication unsupported](ISSUES.md#m04--zip-publication-unsupported) | First publish attempts around 12:13 UTC | Open |
| 03 | [M02: Unrequested planner substituted](ISSUES.md#m02--unrequested-study-planner-substituted-for-blocked-work) | First bot result, message 546 | Open |
| 04 | [M20: Headless vault prompt unavailable](ISSUES.md#m20--interactive-vault-prompt-unavailable-in-api-execution) | Initial login attempts; reported by message 550 | Open integration gap |
| 05 | [M16: Owner clarification sent to teammate](ISSUES.md#m16--owner-facing-clarification-sent-to-a-teammate) | Message 549; repeated at 613/618 | Open |
| 06 | [M11: Ordinary confirmation mishandled](ISSUES.md#m11--normal-confirmations-failed-strict-choice-matching) | Messages 554–557 | Open; historical exact failure partly uncertain |
| 07 | [M24: Retries lacked application feedback](ISSUES.md#m24--retry-lacked-application-rejection-feedback) | Intake failures at 556–557; repeated on later Slack requests | Open |
| 08 | [M25: Intake failure detail discarded](ISSUES.md#m25--intake-failure-details-discarded) | Same failed-intake events | Open |
| 09 | [M10: Reopening left requirements cancelled](ISSUES.md#m10--reopen-retained-cancelled-requirements) | Stop 558, retry 560, reopen 566 | Open; reproduced |
| 10 | [M26: New answer tasks used legacy coordination](ISSUES.md#m26--new-executions-silently-inherited-legacy-semantics) | Answer task for message 561 onward | Open; reproduced |
| 11 | [M19: Help choice got only a promise](ISSUES.md#m19--help-request-got-only-an-acknowledgement) | Answer 561 and response 563 | Open |
| 12 | [M14: Acknowledgement referred to newer question](ISSUES.md#m14--delayed-acknowledgement-answered-a-newer-question) | Answers 569/571/573 and subsequent acknowledgements | Open; mechanism reproduced |
| 13 | [M23: Tool schema/reference mistakes](ISSUES.md#m23--invalid-tool-schema-and-contribution-references) | Connector discovery misuse at 12:29; later finish/contribution errors | Open reliability issue; guards worked |
| 14 | [M21: Slack attempted before ZIP readiness](ISSUES.md#m21--slack-write-attempted-before-required-zip-was-ready) | Delegated write before message 575 | Open orchestration issue; dispatch prevented |
| 15 | [M18: Mistyped reply ID escalated to owner](ISSUES.md#m18--mistyped-internal-reply-id-escalated-to-owner) | 12:48 tool failure and message 576 | Open; exact trigger confirmed |
| 16 | [M03: Outline accepted as website copy](ISSUES.md#m03--outline-accepted-as-website-copy) | 12:50 acceptance and message 577 | Open |
| 17 | [M08: Published website asset relationships broken](ISSUES.md#m08--published-website-assets-lost-their-relationships) | Outline artifact set; later offline-reader index | Open |
| 18 | [M05: Manifest accepted as ZIP delivery](ISSUES.md#m05--manifest-accepted-as-zip-delivery) | 12:52 acceptance and message 581 | Open |
| 19 | [M06: Accepted ZIP evidence stale after changes](ISSUES.md#m06--derived-zip-stayed-fulfilled-after-upstream-changes) | README addition at 582 and later content rebuild | Open |
| 20 | [M07: Slack notification used container-local path](ISSUES.md#m07--slack-completion-linked-a-private-container-path) | 12:55 receipt and message 585 | Open delivery usability; original send succeeded |
| 21 | [M22: Broad recovery diagnosis was overstated](ISSUES.md#m22--earlier-broad-poor-revision-recovery-finding-corrected) | 12:55 stale batch; later non-existent artifact reference | Corrected finding; not a proven persistent retry loop |
| 22 | [M13: Scope confirmations repeatedly requested](ISSUES.md#m13--confirmations-repeatedly-requested) | Messages 591–602 | Open |
| 23 | [M12: Course details requested despite available context](ISSUES.md#m12--clarification-requested-already-available-course-information) | Saved clarification at 593; response 594 | Open |
| 24 | [M27: Clarification replaced immediate user input](ISSUES.md#m27--clarification-replaced-immediate-owner-input) | Clarification path, particularly 593/595; also earlier turns | Open; reproduced |
| 25 | [M15: Duplicate acknowledgements waited behind long work](ISSUES.md#m15--duplicate-acknowledgements-queued-behind-long-work) | Answers 602/605; late responses 603/604/607/608 | Open |
| 26 | [M09: Downloaded filenames lacked extensions](ISSUES.md#m09--descriptive-titles-used-as-extensionless-filenames) | Final artifact set around message 606 | Open |
| 27 | [M01: New Slack send reused completed action identity](ISSUES.md#m01--new-slack-send-associated-with-completed-action) | New send requests 609–624; later isolated replay | Open; reproduced mechanism, historical proposal unavailable |
| 28 | [M17: Failed intake described as missing owner authorization](ISSUES.md#m17--failed-intake-became-owner-did-not-authorize) | Peer response 615 after newer send requests failed | Open |



The owner’s questions about entering the bot container and extracting the ZIP were troubleshooting steps, not proof of a missing ZIP on disk. The suggested `docker cp` recovery was provided; the assistant did not execute that copy or verify it appeared on the owner’s machine. M04/M05 remain the application delivery defects. The follow-up Decisions question established that execution assessments and message intake use separate paths; enabling Decisions did not fix intake.

External constraints: CAPTCHA required human assistance, trial access was exhausted, and Slack history lacked a scope. The later partial extraction was explicitly accepted by the owner (message 622); the initial send had a successful provider receipt; README removal was an authenticated UI action. These facts must not be rewritten as application failures or authorizations to replay effects.

### 2026-10-14 — Engineering history itself was incomplete

#### C33 — Issue/fix history was fragmented and the first register backfilled only selected older issues

- **Status:** Documentation correction completed within available evidence
- **Issue/cause:** Phase reviews and JSON evidence captured many fixes, but the first central register contained mainly the current 28 findings and three historical examples. It did not provide the requested chronological usage history.
- **Approach and outcome:** Create this timeline from the visible chat; individually index every finding row in all six review documents, Phase 1 integration findings, usage reports, controller/tool-gate/coordination review fixes and every current M-series finding. Preserve superseded approaches and explicit evidence gaps.
- **Evidence:** [Engineering register](ISSUES.md); this chronology and linked source records.
- **Limit / learning:** This is manual documentation, not automatic runtime error capture. Original earlier diagnoses and dates have now been recovered where the transcripts contain them. Timestamps and turn mapping are recorded in CHAT-COVERAGE.md. Remaining uncertainty concerns unverified fixes/semantic causes, not a presumed absence of the chat.

### 2026-10-19 — Phase 4 completion acceptance resumed

#### C34 — Reviewed-write acceptance runner had not followed the outcome contract upgrade

- **Status:** Acceptance-tool maintenance verified; private-write exit gate remains pending.
- **Issue / confirmed cause:** Fresh Gmail and Slack preparations used the old exact payload without an outcome ID. The application correctly rejected them with HTTP 422 before creating an operation. The runner's preparation verifier also reconstructed a frozen payload without outcome identity/revision and would reject a valid newer approval.
- **Approach and outcome:** Register a `connector_effect` through the existing native prepare tool, put the returned ID inside `review.payload`, and include stored outcome identity/revision when checking the frozen payload. Keep the failed tasks/manifests. Archive only the settled failed groups after checking zero operations. This changes test tooling, not the application or Hermes.
- **Verification:** Five later typed native reviews prepared successfully. The new regression accepts a valid outcome-bound approval and rejects a changed operation revision. All 446 backend tests, Ruff and the web build pass. [Goal evidence](../test-results/phase4-completion-20261019.json), [runner](../tests/phase4_review_acceptance.py), [regression](../tests/test_phase4_acceptance_evidence.py).
- **Learning / remaining limit:** Acceptance runners are consumers of the same versioned contracts as the app. Historical passing tests did not exercise the upgraded live preparation path. Successful preparation does not prove provider delivery.

#### C35 — Native review calls misplaced outcome metadata and mislabeled rejection as denial

- **Status:** Recorded; application/tool-contract improvements deferred until baseline evaluations.
- **Observed trigger:** After registering outcomes, Gmail, Slack and GitHub preparation attempts failed again. Gmail and Slack native traces show `outcome_id` at the top level of `hermbot_review` rather than inside `payload`. The tool correctly rejected unsupported fields; no connector operation existed. Some model summaries emitted `PHASE4_REVIEW_DENIED` even though no owner had denied a review.
- **Confirmed mechanism / contributing design:** The backend requires nested outcome metadata for upgraded work, while the native review description gives a payload example containing only connection/action/arguments and leaves `payload` unstructured. The initial updated acceptance prompt also described adding the ID without a full nested example. This mismatch made the required placement easy to misread; it does not establish that every production failure has the same cause.
- **Current approach:** Make the acceptance prompt an explicit complete nested call example. This allowed fresh Gmail/Slack/GitHub proposals without changing application guards. Retain these failed model calls as baseline cases. Later evaluate whether a shared typed payload/consistent tool guidance prevents the problem, and require responses to distinguish proposal rejection from an owner denial.
- **Evidence / closure:** Failed task IDs and outputs plus successful replacement manifests are in [goal evidence](../test-results/phase4-completion-20261019.json). Closure needs evaluation of ordinary user-driven runs, including correct owner-decision reporting; prompted acceptance success alone does not close it.

Later October 19 reply-preparation evidence: task `42394913-67ec-400a-b0cd-b1523426bb12` successfully read and checked the parent email, then put `outcome_id` inside `payload.arguments` rather than beside `arguments` inside `payload`. The provider action schema correctly rejected it before a connector operation existed. This is another placement failure in the unstructured review payload, not a Gmail delivery error. A fresh preparation uses the existing typed `hermbot_connector` call, whose outcome field is explicit; with legacy orchestration and no matching standing permission it creates the same exact connector review for the human owner. The failed call is retained, and no external effect is retried.

#### C36 — Historical read verification depended on today's request schema

- **Status:** Fixed and verified October 19 after the owner explicitly authorized C36 as an exception to evaluation-before-fixes sequencing.
- **Issue / corrected confirmed cause:** The stock read verifier hashes the full validated `ConnectorArgs` model. `service.tool_action` deliberately omits empty optional fields before hashing receipt arguments, including `connection`, `outcome_id` and `outcome_key` for connector calls. The verifier does not use that canonicalization. The initial diagnosis described historical schema drift; a current native parent-read receipt reproduced the same failure and source inspection established this more precise cause. This is not evidence of stale deployed application code or corrupted receipts.
- **Evidence:** Omitting those empty fields reproduces all ten original read hashes. Applying the backend's current canonicalization also reproduces the new native parent-read receipt exactly. Original hashes and histories remain intact; the completion evidence preserves the pre-fix failure checkpoint. The stock manifest now records successful verification after correction. [Goal evidence](../test-results/phase4-completion-20261019.json), [current read correlation](../test-results/phase4-gmail-message-id-diagnostic-20261019.json), [read verifier](../tests/phase4_read_acceptance.py), [receipt implementation](../backend/service.py).
- **Proposed approach / learning:** Reuse the exact receipt canonicalization in verifiers rather than maintaining a second interpretation. Preserve request/schema version information if the canonical form changes in the future. Do not silently rewrite old hashes or rerun effects to repair evidence. Add both current and historical default-field cases to evaluations. Independent hash reproduction establishes evidence integrity, not a rerun of the old provider orchestration. Keep this corrected diagnosis alongside the initial error rather than presenting a guessed cause as final.
- **Implementation / verification:** Extracted the existing backend hash recipe unchanged into `service.tool_request_hash`; both `tool_action` and the read verifier now use it. Corrected the synthetic receipt fixtures. A regression first reproduced the failure with an actual backend-generated receipt, then passed after the fix; it checks legacy hash compatibility, idempotent replay and rejection of changed nonempty connection/outcome fields. All **447 backend tests** and Ruff pass; the web image builds. The documented stock `tests.phase4_read_acceptance --verify` now verifies the saved three-provider orchestration, including after local deployment. Stored receipt hashes were not rewritten and no external action was replayed. [Final review](PHASE4-REVIEW.md), [regression](../tests/test_phase4_acceptance_evidence.py), [current evidence](../test-results/phase4-completion-20261019.json). Other issue fixes and baseline evaluations remain deferred.

#### C37 — GitHub issue creation was rejected although its token could still read the repository

- **Status:** Provider access recovered and fresh owner-reviewed issue creation/continuation verified October 19; diagnostic/application improvements remain deferred until baseline evaluations. Related to the earlier setup failure C12, but this is the new October 19 write attempt.
- **Observed failure:** The owner approved issue creation in `dhruvturboml/hermbot-phase4-test`. Operation `e337a0c6-3840-42e8-827d-eb406789228e` received a definitive provider rejection, no successful receipt, and no retry. The bot recorded the required outcome as failed and completed its execution honestly. The connection changed to `needs_reconnect`.
- **Confirmed evidence:** The same saved fine-grained token subsequently authenticated to `/user`, read the selected repository and listed its issues with HTTP 200. The authenticated account owns the repository; it is not archived, issues are enabled, account-level repository permissions include administration/push, and the read request rate limits were not exhausted. Those account permissions do not establish the token's write grant. [Read-only diagnosis](../test-results/phase4-github-write-diagnostic-20261019.json) contains no credential or arbitrary provider response body.
- **Confirmed application mechanism / unknowns:** `connector_http.request` maps both HTTP 401 and 403 to the same credentials-or-scopes exception without retaining the status/reason. `drive_one` consequently marks the whole connection as needing reconnect. Exact original rejection details are unavailable; missing fine-grained `Issues: write` permission is a likely prerequisite problem, not a proven token-grant inventory. Valid reads after rejection do not prove mutation authorization.
- **Current approach:** Ask the owner to verify the selected repository and `Issues: Read and write`, then reconnect privately. [GitHub's create-issue contract](https://docs.github.com/en/rest/issues/issues#create-an-issue) requires Issues(write) for a fine-grained token. After access is corrected, prepare a fresh exact native draft for owner review; retain the failed action and do not replay its old approval. No changes were made to credentials, scopes, application code or Hermes during diagnosis.
- **Proposed design follow-up:** Distinguish expired credentials, action-specific scope rejection, repository restrictions and rate limits through bounded provider-specific error classes. Preserve a safe actionable reason and avoid disabling still-permitted reads solely because a write was denied. Evaluate this together with the existing Slack scope-recovery cases; do not weaken mutation authorization or infer write access from a successful read.
- **Recovery evidence:** After the owner confirmed permission correction, the existing saved fine-grained token was reconnected privately through the supported token route, advancing connection revision 4 → 5. Fresh draft `2c15a59c` received a new exact owner approval. Operation `ab7c041f-a276-43e6-b05f-025f8ee8e450` succeeded with an actual issue ID/number and completed native continuation/report; [verified write](../test-results/phase4-reviewed-github-2c15a59c-71be-428d-92dd-fd3bba711483-verified.json). The earlier rejected operation and approval were not replayed. A separate read-only native inspection verified the actual issue's ID, title and body against the approved write; [readback](../test-results/phase4-github-item-inspection-20261019-verified.json). The original rejection's exact reason was not retained; successful recovery does not fix the broader classification/diagnostic gap.

#### C38 — New-message acceptance did not establish the roadmap's editable Gmail replies

- **Status:** Editable threaded-reply acceptance passed October 19 after exact owner editing/replacement/approval. Broader evaluation findings C35/C39/C40 remain separate.
- **Issue / confirmed cause:** The roadmap names editable Gmail replies, but the live reviewed-write runner's Gmail case constructs a new message with empty `thread_id` and `in_reply_to`. The October 19 edited Gmail success therefore proves editing and approved sending, not reply threading. The checklist remains unchecked rather than replacing this requirement with a narrower one.
- **Approach:** Use the actual successful self-test message as the parent. Preserve its subject, actual provider thread ID and actual Message-ID read from the stored email; C40 disproved the initial assumption that the locally supplied header survived. Require a native `gmail.read` of that exact parent before proposing; stop on mismatched headers. Prepare an exact reply for owner editing/replacement/approval. After success, require a native read of the returned message and verify thread ID, In-Reply-To and References, plus the durable operation/receipt report. Save a unique manifest before submission and never replay the original message.
- **Evidence / closure:** [Reply proof](../test-results/phase4-gmail-reply-20261019-verified.json) independently correlates both native reads to durable canonical receipt hashes, checks the actual delivered owner-edited body, unchanged recipient/subject, matching provider thread, In-Reply-To/References, superseded old approval/operation, successful exact replacement and published native receipt report. Its manifest is `gmail_reply_manifest` in [goal evidence](../test-results/phase4-completion-20261019.json). The verified group is archived with records retained. This completes the acceptance gap through existing application/harness behavior; application issue fixes remain deferred until baseline evaluations.

#### C39 — Bot reported a connector tool missing although its next request advertised it

- **Status:** Confirmed model capability-reporting failure; retained as an evaluation case. No application fix applied.
- **Observed failure:** Native Gmail reply task `d32ecb7e-c724-4ce1-be0a-0ae1fecc7090` prepared its delivery outcome, then finished claiming `hermbot_connector` was not callable. It made no connector read and created no connector operation or send proposal.
- **Contradicting evidence / precise mechanism:** The task's execution plan is ready, has no blockers and explicitly includes `hermbot_connector`. A `capabilities.visible` event after preparation includes that tool; the initial pre-preparation event excludes it. Current authenticated capability inspection also permits it. This disproves the claimed absence at the application projection boundary. There is no attempted invocation/permission error to diagnose as denied access. The model's internal reason for ignoring the later advertised schema is unknown; confusing the initial restricted tool list with the current list is a plausible contributing factor, not a proven application defect.
- **Current approach:** Retain the failed task/manifest and archive its group only after verifying completed executions, zero connector receipts and zero operations. Prepare a separate reply fixture with explicit instructions to use the post-preparation schema and discover connections if necessary. This is a fresh preparation, not a provider-effect retry, and changes no application/harness code.
- **Evidence / closure:** [Goal evidence](../test-results/phase4-completion-20261019.json), `previous_reply_preparations`, records the failure and advertised-tool fact. The original task retains both visibility events and preparation details. Add a baseline case where tools become available after preparation; require actual permitted invocation or a concrete admission error before reporting missing access. A prompted recovery does not establish that ordinary unprompted runs are fixed.

#### C40 — Gmail returned a different Message-ID from the application's supplied header

- **Status:** Confirmed live-provider mismatch; recovery guidance/adapter improvements deferred until baseline evaluations. Reply preparation uses the actual returned header.
- **Observed trigger:** The second native reply preparation read the actual approved self-test email. Its provider message/thread IDs and subject matched, but the stored `Message-Id` was a Gmail-generated value, not the `<hermbot-{operation_id}@local.invalid>` supplied by `connector_providers.execute`. The bot correctly stopped before proposing any reply with the wrong parent header. No reply was sent by this attempt.
- **Confirmed cause / limit:** The earlier preparation inferred the parent Message-ID from application source rather than using the provider's stored message header. That assumption was false for this real Gmail delivery. The precise reason Gmail replaced that supplied header is not established. This does not mean the successful original send or its receipt failed.
- **Current approach:** Use the correlated native `gmail.read` receipt for the exact parent message, verify its actual thread/subject and derive In-Reply-To from its stored Message-ID. A new exact proposal must read/confirm those values again before review, and after sending must verify reply threading/headers. Preserve both failed preparations and verify zero connector operations before starting a fresh preparation.
- **Recovery/design implication:** A stable locally supplied Message-ID cannot be the sole proof/search key for a lost Gmail send. Use actual provider IDs when a definitive receipt exists; treat missing receipts as uncertain and require corroborating provider evidence. Never infer absence and resend merely because the supplied header cannot be found. Add this provider-rewrite case to baseline evaluations; no application/Hermes changes or effect replays were made.
- **Evidence / closure:** The second `previous_reply_preparations` entry in [goal evidence](../test-results/phase4-completion-20261019.json) records the exact parent read and mismatch; the original native receipt is retained on task `2e7a9b85-de86-43ad-8fe9-ea8a4fac5404`. Closure of reply acceptance requires an owner-edited approved reply using actual headers and a verified provider thread; broader uncertain-send recovery remains an evaluation follow-up.

### 2026-10-20 — Phase 5.1 routine acceptance

#### C41 — Chat routine draft used a display name as a bot ID

- **Status:** Verified within the local routine scope. The broader interpretation diagnostics gap in M25 remains open.
- **Observed trigger:** A real owner message asked Scout to prepare an inactive weekday routine. The interpreter returned a valid routine proposal with `bot_id=scout`, while Scout's database ID is `researcher`. The draft insert hit a foreign key, exhausted three intake attempts, and left the original message queued with a generic failure. No routine or future work was created by those failed attempts.
- **Confirmed cause:** The routine schema allowed a syntactically valid bot ID without checking it against the participants before insertion; the prompt gave the model a default ID but did not list display-name mappings. The foreign-key error was a backend trust-boundary failure, not proof that the owner had asked for an invalid bot.
- **Fix:** [work_intake.py](../backend/work_intake.py) now lists eligible ID/name pairs, requires the model to use the ID, and resolves an unambiguous participant display name at application time. [routines.py](../backend/routines.py) validates draft bot membership before writing. This leaves unknown or ambiguous names rejected as a routine-specific validation error.
- **Verification:** The isolated alias regression and [same-message live retry](../test-results/phase5-routines-draft-live.json) passed. The retry produced one inactive draft, no new work item and no external change; it did not submit a second owner message. The original failure remains in the task event history. The application still records only sanitized error classes for other intake failures (M25).

#### C42 — Routine review found lifecycle, authorization and preview gaps

- **Status:** Verified within the local routine scope. These were pre-release code review findings, not observed external provider failures.
- **Confirmed causes:** The first routine implementation delayed invalidating unstarted tasks until a worker could claim them; a stopped bot could leave stale queued work and block group cleanup. An active routine pointed at an archived conversation could resume silently if that conversation was restored before the next due time. A recurring connector effect's saved action was compared with its expected result, but its destination was not. The next-run preview validated an entire unfinished routine form, showing irrelevant name/instruction errors during schedule editing.
- **Fix:** [routines.py](../backend/routines.py) cancels unstarted work in the same transaction as a revision edit/pause/archive, pauses routines when a destination becomes unavailable, and accepts a schedule alone for preview. [product.py](../backend/product.py) pauses active routines at the owner archive action, requiring explicit resume after restoration. [routine_models.py](../backend/routine_models.py) requires the saved action and destination to match the expected external result. [Routines.tsx](../web/src/Routines.tsx) sends only the schedule to preview. Three consecutive definite failures now pause the schedule and surface an inspectable error.
- **Verification:** [Regression results](../test-results/phase5-routines-backend.xml) cover these conditions; [live acceptance](../test-results/phase5-routines-live.json), [post-restart run](../test-results/phase5-routines-restart.json), [mobile browser proof](../test-results/phase5-routines-archived-browser.json) and [final audit](../test-results/phase5-routines-final.json) verify the completed local paths. No new external write was made to test these guards; connected-app authorization and provider receipt behavior is covered by isolated regressions and earlier Phase 4 acceptance.

### 2026-10-25 IST — Phase 5.2 implementation review and local acceptance

These findings were exposed while implementing event triggers, before claiming their local delivery. This is new feature verification, not the deferred broader issue-fix backlog. The local acceptance timestamps are October 24 UTC.

#### C43 — Event collection, admission and history had boundary and concurrency defects

- **Status:** Corrected and covered by targeted regressions in the Phase 5.2 build.
- **Root causes:** Revisited poll events were hashed using collection-time fields, so one logical identity could conflict with a later observation. Daily limits used receipt/creation time rather than actual admission time. Pending capacity was checked before coalescing reuse and combined occurrences lacked a member bound. Admission repeatedly selected the oldest blocked triggers, risking starvation, and acquired a conversation share lock in the reverse order to archive. Sparse Calendar cancellation/Gmail deletion records and non-ASCII signature text violated assumptions in parsing. History navigation used the current trigger destination rather than the execution's original destination; Stop needed the current work execution after retries. Saved source health could stay ready after its collector disappeared.
- **Approach/fix:** Keep immutable raw-body hashes for HTTP and stable logical-identity hashes for polling; count `admitted_at`; reuse eligible occurrences before capacity checks and cap them at 100 members; rotate blocked admission times; take destination locks without waiting; explicitly normalize tombstones and compare signature bytes; use authoritative work/execution identities in history; derive disconnected status from stale monitored check-ins. Preserve event/cursor atomicity, source revision fencing and lifecycle cancellation.
- **Verification:** [Review findings 1–7 and 10](PHASE5-EVENTS-REVIEW.md), [523 backend tests including 44 event-trigger test cases](../test-results/phase52-backend.xml), [real local duplicate/restart/Hermes proof](../test-results/phase52-live.json), [browser evidence](../test-results/phase52-browser.json).
- **Limits:** Live Slack delivery and new provider-change canaries are not established by these isolated tests or read-only provider baselines. Native independently authenticated actions remain outside exact connector receipt correlation.

#### C44 — Result editing repeated a plain-HTTP browser API mistake

- **Status:** Corrected in both the event-trigger and existing time-routine editors.
- **Root cause:** The new editor copied `crypto.randomUUID()` from the time editor instead of reusing the existing HTTP-compatible `requestKey()` helper. The API can be absent on a nonsecure HTTP origin, making **Add expected result** fail. This is the same browser-context assumption recorded historically in C04, in another caller.
- **Fix:** Use the shared helper based on cryptographically generated bytes in both forms. Keep identifiers in the existing UUID format.
- **Verification:** [Browser regression](../test-results/phase52-browser.json) clicks Add expected result in both editors through `http://api:8000`; TypeScript/build pass. [Review finding 8](PHASE5-EVENTS-REVIEW.md).

#### C45 — A valid-looking Compose list prevented collector startup

- **Status:** Exposed during actual local deployment, corrected and verified with a healthy running collector.
- **Root cause:** Commas inside an unquoted flow-style `tmpfs` entry were parsed as separate list values. Docker consequently tried to mount `nodev` as a path and rejected the new service. Python tests and the image build cannot detect this operational parsing error.
- **Fix:** Quote the complete mount/options string. Restart the local services and verify the collector heartbeat; preserve existing bot containers and private volumes.
- **Verification:** Actual Compose startup, health checks and [event/Hermes restart acceptance](../test-results/phase52-live.json); [all three bots/nine volumes preserved](../test-results/phase52-integrity.json). [Review finding 9](PHASE5-EVENTS-REVIEW.md).

### 2026-10-25 — Questions and reviews outlived their work

#### C46 — Project chat retained an expired native review after its task completed

- **Status:** Fixed and deployed locally. All 532 backend tests, Ruff, TypeScript/build and browser checks pass. The reported Project chat review and the obsolete ordinary question are now cancelled, with decision/answer fields still empty and their histories retained.
- **Usage report:** The owner reported that a bot question/review remains on screen after expiry or task completion, with no usable way to remove it, and identified Project chat.
- **Observed instance:** Review `1d1793e1-383e-42fc-b7e4-6d9615ce35ba` remained `pending` for completed writer task `d20e27ef-7d43-4af4-8ba1-fc9673fd1463`, despite its October 21 expiry. This was a native command review, rather than an ordinary information question. A separate expired ordinary question in Project chat temp2 was also still stored as pending after completion.
- **Root cause:** Task cancellation closed pending prompts, but ordinary completion and a native attempt ending did not consistently do so. Expiry cleanup depended on a task deadline instead of the prompt's own expiry. The state endpoint returned all pending reviews regardless of expiry/task state, and the frontend checked the review/question status alone. Decision endpoints correctly rejected the obsolete action, leaving a card whose buttons could never succeed. The waiting-input loop also treated an expired-but-pending question as a live blocker.
- **Fix:** Shared task-locked reconciliation closes obsolete pending approvals and questions, cancels only their unapproved pending app/knowledge/lifecycle proposals, and emits closure events without inventing an answer or decision. An unanswered prompt expiry stops waiting work with an explicit reason; it does not auto-resume as though the owner had answered. Native reviews are bound to a live attempt, and late events from ended attempts cannot recreate them. Shared pending-question reads, the state endpoint and client guards exclude expired/settled requests. Valid budget-paused questions and already decided approvals remain intact; uncertain external effects are not replayed or cancelled by this cleanup.
- **Verification:** [Backend regressions](../test-results/request-lifecycle-backend.xml), [browser checks](../test-results/request-lifecycle-browser.json) cover task completion/failure/cancellation/uncertainty, independent expiry, late native events, preserved live requests, stale client snapshots and the reported Project chat card. This is the owner's explicitly requested fix; the separate evaluation-before-backlog ordering remains unchanged. No Hermes source or external provider action is changed.

### 2026-10-25 — Live provider event acceptance

#### C47 — Legacy receipt reporting accepted a finish that settlement would reject

- **Status:** Fixed and deployed locally; four real canary tasks recovered through actual report-only native continuations, without repeating their provider actions.
- **Observed:** All four owner-approved Google/GitHub canary writes succeeded. Each bot reported the actual receipt, but its legacy task then failed with “The work was not prepared against this teammate's current access.” The saved plan belonged to attempt 1; the receipt report was attempt 2. Access itself had not changed. These direct `/tasks` acceptance tasks use the legacy protocol; Phase 5.2 event-created tasks use managed orchestration version 2 and completed successfully.
- **Root cause:** Legacy preparation intentionally expires on continuation. `hermbot_finish` accepted the report despite that expiry, leaving the worker to reject it only after the bot had ended its turn. The delayed generic access error confused stale bookkeeping with missing permission. The acceptance prompt also omitted an explicit preparation refresh for the receipt continuation.
- **Fix:** For a running legacy work task with a previously ready plan, finish checks the current manifest before accepting the result. Stale preparation returns an immediate, actionable 409: prepare again, then finish using the existing receipts. The existing settlement guard remains. Waiting reviews, blocked plans, discussion and managed work retain their existing behavior. Fresh canary prompts explicitly refresh preparation on continuation. A guarded, once-only recovery of the four exact failed tasks checked their successful frozen operations/fulfilled outcomes, retained the original failure events and resumed report-only work. No approval was invented and no effect was replayed.
- **Verification:** The continuation regression rejects the first finish without storing a pending result, refreshes preparation, accepts the retried finish and actually settles successfully. [Full backend results](../test-results/phase52-provider-backend.xml), [real canary task/receipt identities](../test-results/phase52-provider-events.json). Recovery audit events use `task.recovery_requested` with stable source IDs.

#### C48 — Drive server versions were mistaken for distinct visible file changes

- **Status:** Fixed and deployed locally. All five provider checks, including the separately approved Google Doc and its read-only creation verification, pass. The original failures remain recorded.
- **Observed:** A converted Google Doc returned version 4 in its successful create receipt. The Changes API subsequently returned versions 6 and 7 under the unchanged name; two unwanted event reports ran before the owner's real rename. The owner's rename itself produced one correct report. The acceptance verifier initially could have counted a creation echo as positive input; it now requires the explicit external-change marker and correct resource identity.
- **Root cause:** Drive's server version covers invisible changes as well as user-visible state. Both event identity and self-effect correlation relied exclusively on that version. The observer had no stable file-state comparison, and the create receipt did not request modification time, parents or trash state. [Google's file reference](https://developers.google.com/workspace/drive/api/reference/rest/v3/files) documents the server-version distinction; the precise internal Google operation behind each increment is not established.
- **Fix:** Persist a fingerprint of the observed name, MIME type, modification time, parent IDs and trash/removal state. Skip version-only changes with the same prior fingerprint. Request those fields directly in new Drive create/upload receipts and correlate equal receipt state despite a different server version. The fresh test proved conversion changes modification time too, so state comparison alone did not close the issue. For the first observation of a marked Google Doc created after the source baseline, the collector verifies its successful creation receipt and exact initial plain text/name/folder using one export outside database transactions. That proof follows only the unchanged observed state and is stored atomically with the page; later text changes/renames/moves/removals stay distinct. Initial text already edited by a human does not correlate. Missing state falls back to conservative version handling. No time-window/account-wide suppression or network request inside admission was added. Drive pages are bounded to ten changes so initial exports fit the collector lease.
- **Verification:** Regressions cover invisible version increments, receipt-state correlation, initial approved/edited text, proof retention across another invisible version, later human edits/rename/move/trash/removal, sparse records and old state without the fingerprint. The actual approved Doc was checked against its live export by the collector function; guarded reconciliation recorded the unchanged state for its retained, never-admitted event, then ordinary admission marked it ignored with no task. No additional provider write was made. [547 backend tests](../test-results/phase52-provider-backend.xml), [live provider evidence](../test-results/phase52-provider-events.json).
- **Limit:** The Drive event contract observes the fields above; permission-only changes and rich-format-only changes before the initial plain-text comparison are outside that contract. Polling cannot reconstruct intermediate changes overwritten before observation. Independently authenticated native effects retain their existing coverage limit. These are explicit limits, not claims of universal loop prevention.

#### C49 — A full daily allowance deferred self-event reconciliation

- **Status:** Fixed and verified locally during the final Drive acceptance.
- **Observed:** The fresh Doc's initially unmatched creation event remained pending behind the three-run daily allowance even after a definitive own-write proof was available. No duplicate provider effect occurred, but normal admission could not classify the event as ignored that day.
- **Root cause:** Admission checked the consumption cap before examining finalized receipts. A limit on starting new work also prevented a decision that starts no work and consumes no allowance.
- **Fix:** Validate the event/source and check own-write correlation before applying the daily admission cap. Keep the cap before any real task creation. Backlog, authority and expiry protections remain intact.
- **Verification:** A regression consumes the day's allowance with a genuine event, records another event before its own receipt, adds that successful receipt and verifies the pending event becomes ignored with no second task. The retained live Drive event also became ignored through ordinary admission after the guarded content verification. [Backend results](../test-results/phase52-provider-backend.xml), [provider proof](../test-results/phase52-provider-events.json).

### 2026-10-26 — Phase 5.3 implementation and focused review

These are integration/review findings in the new recurring-manager implementation, not newly inferred incidents in the older Manik chats. Causes below are confirmed by source tracing and focused regressions. [Full review](PHASE5-ORCHESTRATION-REVIEW.md), [backend checks](../test-results/phase53-backend.xml), [native receipts](../test-results/phase53-live.json) and [browser proof](../test-results/phase53-browser.json) retain verification. Actual owner-answer continuation and final question-fixture cleanup subsequently passed, completing local milestone acceptance. These fixes do not close the old issue backlog.

#### C50 — Task inspection code broke the workspace state endpoint

- **Status:** Fixed; API regression and deployed state/browser checks pass.
- **Observed:** The first 5.3 live acceptance attempt received HTTP 500 from `/api/v1/state` before creating fixture work.
- **Root cause:** Task-specific selected-scope enrichment was accidentally placed outside a task context, referencing an undefined local variable. A narrow feature check did not exercise the workspace endpoint.
- **Approach/fix:** Keep enrichment in task inspection and test authenticated workspace state plus the narrowed task scope together. The first failed attempt created no test work; later native demonstrations ran through the corrected endpoint.
- **Learning:** Shared state APIs belong in the minimum integration check for task metadata changes, even when the feature's core policy tests pass.

#### C51 — Display reassociation could detach automated work from its occurrence

- **Status:** Fixed and verified with reclassification, accounting, continuation, overlap and cancellation regressions.
- **Observed/design defect:** Tracing owner message correction showed that mutable work/intent fields could be cleared while the admitted execution continued. A lookup using only those fields would lose recurring scope and shared budget/stop coverage.
- **Root cause:** Display classification doubled as the durable authorization/accounting association. An automated peer response also has a separate execution root, so original-root-only grouping was insufficient.
- **Approach/fix:** Add the indexed static `management_work_item_id` for admitted automated work, freezing scope on the occurrence's work record. Use it for authority, budget, locking, overlap and owned stop. Preserve normal cosmetic correction behavior. Do not infer authority from thread proximity or attach unrelated owner discussion to an occurrence.
- **Learning:** A mutable presentation link cannot be the only identity for immutable execution policy. Preserve provenance independently without creating a second workflow database.

#### C52 — Selected cancellation authority implicitly depended on inspection

- **Status:** Fixed and verified; delegate+cancel permits its owned cancellation boundary and rejects ordinary inspection.
- **Observed/design defect:** A valid selected operation subset could pass configuration validation and then fail because cancel/steer/reassign reused an inspection helper.
- **Root cause:** Ownership lookup and inspect authorization were coupled. Adding selected-operation enforcement to that helper unintentionally imposed an extra operation on all callers.
- **Approach/fix:** Pass the actual requested operation through the shared owned-descendant check. Keep inspect as the default for true inspection and retain separate manager/grant validation at mutation callers.
- **Learning:** Reused lookup helpers must not silently add permissions unrelated to the caller's action. Configuration, discovery and operation enforcement must describe the same effective scope.

#### C53 — Native completion prose could misstate recurring success

- **Status:** Fixed with missing-result/failure regressions, final acceptance guards and verified native contribution receipts.
- **Observed/design defect:** A manager could end a native turn without accepted required results while the execution itself recorded completion. Original-root-only failure counting also missed later successful repair of the same occurrence.
- **Root cause:** Harness turn completion was being used as evidence of responsibility completion, and execution-tree identity was narrower than the work's lifetime.
- **Approach/fix:** Reject final acceptance while required or optional automated work is unsettled. If a manager ends without accepted required results, record explicit failure and stop remaining owned execution. Count definite failures from current settled occurrence work/results. Advance incremental windows only from verified compatible success, including equivalent grant renewal, and after attempts/effects settle.
- **Learning:** Completion comes from accepted current results and actual effect settlement. Preserve partial receipts and uncertainty; neither model prose nor a successful final summarizer proves the whole responsibility succeeded.

#### C54 — Managed reply admission could invert revocation's lock order

- **Status:** Fixed; concurrent revoke/claim and locked-occurrence peer-admission regressions pass.
- **Observed/design defect:** The newly authority-bound peer path could validate/hold a grant before acquiring its work lock, while revocation acquired work before updating the grant. Those two orders can wait on each other.
- **Root cause:** Adding a live shared authority lock to an existing separate-root admission path required tracing all work/root/grant acquisition boundaries, including automated informational replies.
- **Approach/fix:** Claim canonical work/root nonblockingly before validating authority in peer admission. Align dispatch/revoke canonical work identity and skip a busy occurrence for a later pass. Replies inherit scope/run/thread/budget; revocation cancels owned pending execution and messages without fabricating effect receipts.
- **Learning:** Authority checks that acquire locks must share a documented acquisition order with mutation and recovery paths. A global scheduler claim lock does not serialize owner/API revocation.

Final acceptance-driver follow-up: the actual owner answered the waiting 5.3 question and the same native session completed its two-contributor report. Cleanup initially used a nonexistent POST conversation-archive route and received HTTP 405, after the fixture routine/grant steps succeeded but before group archival. The driver was corrected to use the existing owner DELETE group endpoint. Guarded cleanup then passed with original owner bots, groups and active routine definitions preserved. This was a test-driver endpoint mismatch; the product archive API was unchanged. Evidence is retained in [phase53-live.json](../test-results/phase53-live.json) and [the review](PHASE5-ORCHESTRATION-REVIEW.md).

### October 26, 2026 — Phase 5.4 attention implementation and review

These are defects exposed during implementation/controlled verification or confirmed by connected-path review. They are not additional owner-reported usage incidents, and they do not close the deferred historical backlog. [Review/evidence](PHASE5-ATTENTION-REVIEW.md). Source order within this same-day review is retained; individual review findings have no separate incident timestamp.

#### C55 — Push timeout extension changed the existing connector deadline

- **Status:** Fixed; the original slow-drip transport regression and cumulative 638-test suite pass.
- **Observed/design defect:** Adding a push-specific timeout captured the module timeout constant in a default argument; changing the runtime test deadline no longer interrupted the response.
- **Root cause:** A shared transport parameter changed call-time configuration semantics while adding a new caller.
- **Approach/fix:** Resolve the optional timeout at call time, retain existing raw-response callers and add explicit push response metadata through the public library session hook. Preserve pinned public-address sockets, original TLS hostname, no redirects/proxies and bounded response reads.
- **Learning:** Extending a shared trust boundary must preserve its established timing and return-value contract for all callers.

#### C56 — Catch-up eligibility could use unrelated unread work

- **Status:** Fixed with current-member, empty-digest and read-before-send regressions.
- **Observed/design defect:** A summary whose own covered questions had resolved could still send because a different unread question existed elsewhere.
- **Root cause:** Summary transport identity was present, but eligibility was checked against the entire inbox instead of that summary's members.
- **Approach/fix:** Recheck actual covered item/version references and their freshness; overflow summaries alone represent general fresh backlog. Do not self-merge a summary into itself.
- **Learning:** A grouped action needs explicit membership and a current-state predicate, not merely a shared label or any remaining unread row.

#### C57 — Quiet-period and overflow replacement could repeat or lose interruption

- **Status:** Fixed; DST-fold, covered-member policy replacement, saturation, burst and overflow-only replacement regressions pass.
- **Observed/design defect:** Repeated local hours could yield two quiet-end keys. Preference changes could suppress a pending summary after its individual records were marked covered; overflow-only backlog had no item row to recreate.
- **Root cause:** Period identity, policy revision and summary coverage were treated as interchangeable queue attributes.
- **Approach/fix:** Key quiet periods by explicit local period/policy/browser generation; retain fresh covered members and one overflow obligation across policy replacement. Reserve one of 1,000 pending positions for overflow and count actual dispatch attempts for the three-per-minute cap. Do not replay stale work or drop the in-app record.
- **Learning:** Superseding policy must preserve the durable obligation while invalidating only the obsolete delivery representation.

#### C58 — Slow push could delay the attention projector

- **Status:** Fixed; a blocked-send regression verifies a real database question still projects concurrently.
- **Observed/design defect:** The first notification worker performed projection and outbound delivery sequentially, so a slow vendor could hold new inbox state behind HTTP.
- **Root cause:** Moving notifications out of bot execution did not itself separate the latency of attention from the latency of its transport.
- **Approach/fix:** Independent projection and sending pumps run inside the same small control-network service, without model credentials or bot slots.
- **Learning:** Separate loops when their availability/latency contracts differ; a separate container alone does not remove internal coupling.

#### C59 — Retention could reverse the delivery lock order

- **Status:** Corrected by connected-path review; cumulative/concurrency checks pass. A production deadlock was not reproduced.
- **Observed/design defect:** Delivery completion acquired delivery before subscription/preferences, while initial reconciliation/retention held preferences/subscriptions before updating expired deliveries. An expiring in-flight record can complete that cycle.
- **Root cause:** Maintenance was treated as harmless because it did not execute work; it still acquired the same database locks in another order.
- **Approach/fix:** Commit maintenance families independently; derive server-key health from subscription state rather than updating preferences while holding a delivery lock. Use fenced short claims and send outside transactions.
- **Learning:** Include maintenance, health and recovery in the same lock-order analysis as the main mutation path.

#### C60 — Source navigation could inherit recent-state and cosmetic-association limits

- **Status:** Fixed with old-target, reclassified occurrence, running-result settlement and published-file regressions.
- **Observed/design defect:** Old tasks outside the recent state slice could open incomplete inspection. A mutable display work link could detach a notification's occurrence or its published files.
- **Root cause:** Reusing a recent UI slice/display association as durable source identity rather than reusing the canonical domain reader.
- **Approach/fix:** Resolve exact authenticated source/work/thread identities; add an explicit bounded canonical-occurrence read option and use it for both execution and artifact links. Coordinate conversation/thread navigation and supplement inspection with exact fetched task/children.
- **Learning:** Durable links require durable identities and independent reads. UI pagination and reversible display corrections must not change execution/result identity.

#### C61 — A browser endpoint alone did not identify the registration generation

- **Status:** Fixed; encrypted registration/key rotation, stale lease, per-device error and current-browser logout regressions pass.
- **Observed/design defect:** The same endpoint could be re-registered with new browser keys without changing its generation. An old completion could then affect a replacement registration.
- **Root cause:** Endpoint URL equality was mistaken for equality of the full capability/credential lifecycle.
- **Approach/fix:** Fence by the endpoint, key material, configured origin/server key and registration generation. Retain safe replaced records, retire only the failing current generation and revoke the current browser explicitly on logout. Normal session expiry remains separate.
- **Learning:** Credential replacement needs a generation fence even when a resource address stays unchanged.

#### C62 — Reused review errors were outside the visible inbox surface

- **Status:** Corrected by UI-path review; authenticated/responsive screens and current review/answer refusals are verified. No real external action was approved for this check.
- **Observed/design defect:** Review actions reused the conversation action wrapper, which put a rejection in the underlying page banner while Attention covered that page.
- **Root cause:** Reuse carried an implicit presentation context along with the action behavior.
- **Approach/fix:** Send errors to the visible Attention panel while preserving the exact existing owner decision handlers and their final stale-state/authority checks.
- **Learning:** Reuse action semantics without assuming that the original error surface remains visible in a new modal or navigation context.

#### C63 — Proven upstream setup failures could multiply downstream alerts

- **Status:** Fixed; upstream repair/downstream exposure regression passes.
- **Observed/design defect:** A connection needing reconnection, its source needing access and the dependent paused trigger could each create a setup alert for the same known chain.
- **Root cause:** Per-object deduplication did not account for explicit dependency references.
- **Approach/fix:** Inhibit dependent symptoms only when stored connection/source foreign keys and current failure states prove that dependency. Repairing the upstream object exposes any still-unresolved downstream setup. Keep independent owner decisions separate.
- **Learning:** Group proven causes, not similar prose or nearby timestamps. This is attention inhibition, not the deferred cross-service work-ownership project.

Implementation/driver corrections retained for completeness: an overbroad canonical-artifact edit also touched the separate revision reader and produced an import-time indentation error; it was caught by the verifier and restored before deployment. An ambiguous evidence query was simplified away. Read-history pruning initially allowed detail rehydration during replay and now retains a small tombstone. The native driver used numeric `attempts` instead of `executions`, then reused an empty pending-question list after the real answer; both were corrected against the same retained task. The host build lacked its Rollup native optional package; the pinned container build is the verified path. No replacement owner answer or approval was fabricated to bypass these failures.

#### C64 — Chrome push accepted but no desktop notification reported

- **Observed:** October 26, 2026. The owner enabled Chrome notifications and reported no notification with the Hermbot tab open. Both original test deliveries were accepted by `fcm.googleapis.com` on their first attempt; quiet hours were off and the notification worker was healthy. Vendor acceptance alone does not establish browser receipt or an OS banner.
- **Status:** Resolved by owner configuration, October 26, 2026. The owner enabled notifications for Chrome and confirmed that notifications now work. No application push-transport defect was established.
- **Root cause:** Chrome's notification setting prevented display, as confirmed by the owner. Hermbot had recorded transport acceptance only; browser receipt and notification creation were initially indistinguishable. Direct inspection through Apple Events was denied by macOS; no browser permission was bypassed. The owner did not specify whether the changed setting was site-level or the OS permission for Chrome.
- **Investigation and cleanup:** Temporary opaque-delivery receipts, browser checks and a local display test were deployed to distinguish delivery stages. After confirming the configuration fix, the owner requested their removal. Removed the diagnostic UI/state, automatic browser checks, receipt routes/schema/settings fields, extra push-payload field and diagnostic-specific tests. Retain ordinary push, preferences, registration, the original Send test notification button, safe malformed-payload handling and worker activation. Historical diagnostic events/evidence remain records; they are not ongoing instrumentation.
- **Verification:** Before removal, 642 cumulative backend tests and five controlled worker tests passed. Cleanup verification is recorded in `test-results/phase54-diagnostic-removal.xml`; the retained worker tests verify generic display, target preservation, malformed payloads and absence of diagnostic network requests. Actual display is confirmed by the owner, not inferred from tests.
- **Closure boundary:** This missing-notification incident is closed. Closed-tab current-result navigation and guarded fixture cleanup subsequently passed with actual owner confirmation and preservation checks; Phase 5.4 and the separate Phase 5 exit are accepted for the local scope.
- **Learning:** Instrument independent delivery stages and describe only the strongest evidence observed. Do not equate push-service acceptance or a resolved browser API call with a visible OS banner.

Phase 5 exit verification update, October 26, 2026: the owner confirmed Hermbot tabs closed with Chrome running; a fresh normal result push was accepted, then explicitly confirmed the newest notification opened the exact report. Guarded fixture cleanup and owner-state preservation checks subsequently passed. Fresh scheduled and signed-local-event manager runs, two identical event replays, active-attempt backend restart recovery, 638 cumulative tests and unchanged Hermes/private-volume checks pass. Both temporary test groups/routines, the exit trigger/source are archived, their grants are revoked, and original owner groups/configuration/active routine/browser preferences are preserved. Driver-only schema-query, decrypted-credential-field and closed-pool reuse errors were corrected without altering product data or resubmitting work; details are retained in [the exit review](PHASE5-ATTENTION-REVIEW.md#phase-5-composed-exit--october-26-2026). No new product defect is claimed from those driver errors.

### Coverage and maintenance

- The history includes all **85 finding rows** in the six repository review documents, including both Phase 4 tables, plus individual usage/support issues and review fixes stored in JSON. Some entries intentionally cross-reference the same incident; entry counts must not be presented as unique bug counts.
- M01–M28 occur once each in the current-incident timeline; their detailed root causes, proposals and closure checks stay in the register. H01–H03 remain stable references to historical corrections.
- Original transcripts resolve the formerly unqualified C13/C16A/C23/C27 reports. The Kaggle, revoked-token, Starlink and temp2 findings are individually backfilled above. Follow-up turns are explicitly mapped in [CHAT-COVERAGE.md](CHAT-COVERAGE.md); duplicate/interrupted prompts do not manufacture extra bugs.
- Dates come from original transcript timestamps, report text or evidence timestamps, never filesystem modification times. UTC/IST distinctions are stated for overnight incidents; source-order groupings retain uncertainty for individual review findings without timestamps.
- Add new incidents at their occurrence stage, retain dated diagnosis/fix updates, and link regressions to the original issue. Preserve the difference between a model failure, an application bug, a guard that correctly prevented an action, and an external prerequisite.

### 2026-10-14 verification correction

The claim that earlier diagnoses were unavailable was premature. The original local transcripts existed and were readable. Verification recovered concrete failures omitted by the first backfill and replaced the former generic gaps. This is a documentation/investigation failure: reviews alone are insufficient evidence of complete usage history. Require the original-turn coverage check before claiming completeness. The register now preserves both that error and its correction.

Consolidation update, 2026-10-14: the owner pointed out that the chronology had been split away from the issues file. Chronological history and detailed findings are now maintained together in this canonical register. The former history file is only a compatibility pointer; original-chat coverage remains supporting evidence.

#### C65 — Teaching capture and publication integration defects found before owner acceptance

- **Date/status:** October 27, 2026. Implementation review defects corrected; real teaching acceptance pending. These are newly introduced feature/integration findings, not historical reports of existing Secure input leaking secrets.
- **Root causes:** generic route precedence, conflating prepared and running collectors, confusing fetch progress with durable acknowledgement, asynchronous capture/control revisions, globally installed skill state versus task-specific preview needs, and insufficient binding between tests and current evidence/configuration. Accessible names also included nested option/editor text.
- **Approach/fix:** specific route precedence; explicit collector revision zero; acknowledge committed prefixes; privacy/control generation fences; owner-private encrypted chunks/revisions; task-authenticated candidate middleware; exact source/draft/config test checks before native CAS/readback/refresh; explicit accessible names. The provider's initial HTTP 400 was a rejected token-limit parameter, not proof of missing image support.
- **Verification:** 654 backend tests including 16 teaching checks, responsive synthetic UI and disposable native RFB/desktop checks passed. [Detailed findings, evidence and acceptance limits](PHASE6-TEACHING-REVIEW.md). The existing underlying Hermes source remains unchanged.
- **Remaining:** actual owner demonstration, reviewed generation, fresh-input/negative/collaborative results, installed-skill use and scoped cleanup. Approval review rejected automated typing in the live owner desktop; no such input ran. Fixture evidence does not close the real acceptance gate.

#### C66 — Escape hides teaching capture without an acknowledged outcome or recovery entry

- **Date/status:** October 27, 2026 report; November 2 implementation. Confirmed pause, persistent retained-prefix status, session restoration, remote-focused Escape handling and unsaved-edit guards are implemented and fixture-tested. Real post-fix owner recording feedback remains to verify.
- **Observed:** Esc dismisses the Computer dialog without telling the owner whether capture was retained. Read-only metadata inspection found the owner's Scout recording paused with 34 retained observations, 445,195 bytes and ten persisted chunk manifests, with no recorded error. These are observations, not 34 semantic actions, and metadata does not prove every possible in-flight event was saved. No owner screenshot or typed content was inspected.
- **Confirmed causes:** `Panel` adopts Radix's default Escape dismissal. `Computer` performs an unacknowledged `fetch` in effect cleanup, reusing `teaching-private-input`; it neither checks HTTP success nor exposes failure. Native viewer disconnect independently pauses capture, but its state is shown only inside the dismissed view. `Teaching` remounts with hidden UI and an empty selection, and its recording indicator exists inside that same view. The close and private-input paths consequently share a misleading pause reason/omission marker. Radix's document-capture Escape listener can also dismiss the outer dialog while the embedded noVNC keyboard owns the focused key.
- **Reproduction:** [Synthetic browser diagnosis](../test-results/phase62-escape-diagnosis.json) intercepted all teaching writes and computer state. Esc dismissed the dialog; a synthetic pause HTTP 503 was ignored; no status remained outside; reopening selected the empty New demonstration option. No live desktop connection, input, recording mutation or publication was performed.
- **Recommended contract:** remote-focused Escape goes to the remote application; local Escape/X/backdrop use one pause-and-hide operation. Keep a compact recording status outside the dialog with Resume, Finish and review, and an exact retained-data indication. Separate pause acknowledgement, durable-prefix persistence and final review readiness. Restore the relevant session automatically on reopening/reload, but never resume capture, reclaim control, generate or publish automatically. Distinguish view closure, private input, disconnect and storage failures. Discard remains explicit; warn about genuinely unsaved editor changes rather than prompting on every non-destructive close.
- **Persistence/recovery:** the existing PostgreSQL session/chunk manifests stay authoritative. Surface pending save and unavailable acknowledgement honestly; a request being sent or a view disappearing is not a save receipt. Reconcile by session/revision/sequence after retry or reload; retain and label partial data. Keep browser-close/disconnect protection as a fallback, not the principal save mechanism. No new video engine, queue or Hermes modification is required.
- **Research:** [Radix dialog defaults/hooks](https://www.radix-ui.com/primitives/docs/components/dialog), [W3C dialog keyboard pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/), [Apache Guacamole's remote session/menu separation](https://guacamole.apache.org/doc/gug/using-guacamole.html), [Loom's independent recording controls](https://support.atlassian.com/loom/docs/hide-the-recording-controls), [Loom's Stop-to-review flow](https://www.atlassian.com/software/loom/resources/guides/steps-to-get-started-in-loom), [Chrome Recorder's saved recording list](https://developer.chrome.com/docs/devtools/recorder/reference), [Chrome page lifecycle limits](https://developer.chrome.com/docs/web-platform/page-lifecycle-api), [March 2026 MediaStream Recording draft](https://www.w3.org/TR/mediastream-recording/), [W3C accessible status feedback](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html), [OBS incremental recovery rationale](https://obsproject.com/blog/obs-studio-hybrid-mp4). These inform the design; they are not selected dependencies. The owner-facing teaching format remains actions/keyframes.
- **Close when:** local and remote Escape scopes, X/backdrop, reopen/reload, finalization races, empty/prepared recordings, server/storage failure and genuine unsaved edits produce accurate accessible status; no dismissal discards capture or changes computer ownership. Real owner recording acceptance remains pending.

#### C67 — Colour-band generation gate misclassified model capability

- **Date/status:** October 27 owner report; diagnosed October 28; corrected November 2, 2026.
- **Observed:** Scout's reviewed recording remained ready with 295 observations and 2,306,764 retained bytes, but draft generation failed with “Vision check failed; choose a model that reads images in Settings.” The failed raw output was not retained, so the exact mismatched/missing colour answer is unknown.
- **Cause:** a per-generation synthetic six-band image required at least five exact colour names in order. Incorrect visual answers were treated as proof of absent vision support, bypassed repair and discarded the generation. This tested an unrelated model answer rather than the owner's workflow; the owner-facing diagnosis exceeded the evidence.
- **Correction:** remove the colour challenge and send only selected actual evidence. Validate skill/learning output, allow bounded format repair, and report distinct safe provider/transport/schema failures. Functional tests and owner verification establish procedure quality; accepting an image request alone does not prove understanding.
- **Verification:** selected-input/no-colour-gate regression, exact token-parameter compatibility fallback and HTTP authentication/rate/request-error checks. Reviewed source and recording are preserved. [Implementation review](PHASE6-TEACHING-REVIEW.md).

#### C68 — Teaching test clarifications never became reusable skill knowledge

- **Date/status:** Owner Zenpad usage October 27–28; implementation authorized and corrected November 2, 2026. Real revised-skill pass/publication is still pending.
- **Observed:** `hermbot-zenpad-temporary-notes` was saved and consulted in two tests. The latest test reported Wikipedia verification and note entry, then failed clean-note verification because old text remained; it stopped at the owner's direction. The saved candidate still contained generic prerequisite/target placeholders despite owner clarifications. No owner verification or teaching publication existed at inspection.
- **Root causes:** the plan decomposed capture → draft → test → publish without a feedback-to-revision contract. Generation read only original reviewed evidence; tests froze candidates without a successor-draft process; reporting stored a verdict but no revision. Validation checked Markdown shape, not a usable teaching experience. Tests checked component mechanics without requiring a clarification to improve a subsequent run. Earlier status summaries also wrongly kept all real owner recording/testing in the “not done” category.
- **Correction:** encrypted, source-linked refinement snapshots; scoped owner answers/corrections and actual recorded outcome states; bounded automatic successor drafts after tests settle; session/version-bound questions; explicit reusable/one-run answer scope; fresh test suggestions; immutable old candidates and passes; revision/provider/token fences; publication checks. Historical tests require an explicit improvement action rather than migration silently replaying old conversations. The owner should not need to maintain Markdown. Hermes remains unchanged.
- **Review corrections:** persist the answered question in the same decoded report; invalidate old questions after manual edits/evidence changes; include authenticated `owner_message` answers; retain feedback across failed jobs; preserve complete owner directions within input limits; exclude unrelated and non-owner answers; block publication during unresolved processing/questions. A task ending is not a check pass.
- **Verification/limits:** [32 teaching regressions](../test-results/teaching-feedback-targeted.xml), full 670-test regression run plus final targeted deduplication checks, [intercepted Chrome fixtures](../test-results/teaching-feedback-ui/report.json), pinned frontend build and lint. No live desktop input or owner publication decision is simulated. Raw page text and model claims cannot authorize operations; semantic generalization still needs functional verification.

#### C69 — Expired provider credential blocks real teaching refinement

- **Date/status:** November 2, 2026. External prerequisite pending owner credential replacement; implementation remains deployed and automated checks pass.
- **Observed/cause:** the existing Zenpad Improve action queued normally, then the configured provider rejected it. A separate minimal text-only probe, containing no owner evidence, confirmed HTTP 401 with `expired_secret_key`. The saved credential matches the private environment value. This is independent of the removed colour-band validation defect and is not evidence of missing image support.
- **Handling:** preserve recording, evidence, draft and exact test/refinement history; do not retry indefinitely, swap providers or change secrets automatically. Owner replaces the key privately in Workspace settings → Providers → Environment provider → Edit, then retries the saved refinement. Expiry now has specific owner-facing guidance; raw provider error bodies and credentials are never projected.
- **Verification:** exact-expired-code and generic authentication/rate/request failure regressions; no external browser action or native publication ran. Real revised skill verification remains pending this credential update and subsequent owner acceptance.

#### C70 — Model authentication assumed static API keys across separate callers

- **Date/status:** November 2, 2026. Owner-requested subscription support is implemented/deployed locally; real account acceptance remains pending. This is a design limitation exposed by C69's unusable provider credentials, not a claim that exhausted credentials were repaired.
- **Cause:** provider records/outbox/bootstrap assumed a static key and generic custom endpoint; intake, teaching and assessments independently called Chat Completions/Decisions. Native Hermes subscription login alone would neither survive current application provisioning correctly nor supply the application's own model jobs. Copying renewable credentials to every bot would also create exposure and refresh-ownership problems.
- **Approach:** original official-flow implementation in a host credential broker; Keychain-backed encrypted vault; explicit auth/transport/capability distinction; restricted runtime credentials; shared application transport; one refresh owner; tested models; safe failures and versioned account authority. Underlying Hermes source remains unchanged.
- **Review corrections/verification:** stale status/quota/test results are fenced against disconnect/reconnect, uncertain rotations are preserved without replay, slow consumers and saturated inference queues cannot prevent disconnect, callback/identity validation and scopes are covered, key rotation retains recoverable keys, private environment updates are atomic, and the service restart helper stops its singleton before preparation. Full isolated suite 697 passed before final host setup/key-rotation additions; focused results and native/browser/source/deployment checks are linked in the [review](CHATGPT-SUBSCRIPTION-AUTH-REVIEW.md).
- **Close when:** actual owner consent/eligible model tests and a real no-effect tool/image flow pass, while preserving existing providers, owner data and teaching acceptance boundaries. Host administrator/owner-UID compromise and remote/team deployment remain explicitly outside this local credential-isolation guarantee. No account secret is recorded here.

#### C71 — Subscription implementation selected a different authorization route than the owner intended

- **Date/status:** November 2, 2026. Direct Codex adapter implemented; owner-account acceptance remains pending. The first route is preserved separately, not represented as a working Codex connection.
- **Symptom:** The initial new-application browser flow reported “Your organization hasn't enabled access for this app”, while the owner reported other platforms could sign in. The earlier administrator-only explanation was narrowed: the error does not establish the account's exact policy or the application's eligibility.
- **Root cause / approach error:** Choosing the newer public Responses plan-sharing registration before proving the requested direct Codex route. Local execution and mocked protocol tests did not establish OpenAI account eligibility. Delegating login to Hermes was also not what the owner requested; Hermbot needs to own authentication while leaving the harness unchanged.
- **Correction:** Separate `codex_oauth` connection, original browser/device authentication in the host broker, fixed Codex destination, retained API-key usage, restricted runtime credentials, shared refresh ownership and fair bounded request scheduling. Existing providers/defaults/bot assignments are preserved. No other application's credential file is imported.
- **Review defects fixed:** older broker protocol could otherwise misroute a new auth kind; migration replay could reinstall an incompatible constraint; a broker restart could leave sign-in polling forever; auxiliary routes could survive a switch back to an API key; and ASGI cancellation could prevent its own upstream cancellation message. Queue activity is explicit and is not counted as progress. Scope-specific protocol tests prevent applying plan-sharing tool namespaces to Codex.
- **Evidence / closure:** [Implementation and review](CHATGPT-SUBSCRIPTION-AUTH-REVIEW.md#direct-codex-connection), isolated backend/stream/cancellation fixtures, browser checks, unchanged native source and owner-data snapshots. Close only after real authorization, model/tool/image acceptance and supported backend processing pass. Third-party source is not proof of future hosted/commercial eligibility.

#### C72 — Codex catalog used the legacy fallback and refresh left the picker stale

- **Date/status:** November 2, 2026. Fixed and verified against the connected account's catalog; inference was not submitted during diagnosis or refresh.
- **Symptom / confirmed cause:** Owner could not find newer models. The broker requested only `client_version=0.0.0`, which returned four visible models. Read-only comparison with `99.0.0`, the preferred catalog sentinel in pinned Hermes, returned eight visible models. Two service-hidden entries were excluded correctly. Separately, refresh updated `discovered_models` but populated the picker's `models` only on the initial empty list, so fixing discovery alone would leave existing connections stale.
- **Correction:** Prefer the newer catalog with the legacy fallback on an empty/error response. Refresh an unrestricted model list while preserving explicit subsets, concurrent owner edits, assigned/default models and existing verification. No default/model assignment change and no automatic inference verification.
- **Evidence:** 56 focused auth/catalog tests pass, including preferred/fallback lookup, unrestricted refresh, restricted lists and concurrent edits; lint and app build pass. Deployed API/broker and refreshed the actual catalog: GPT-6.1 Sol/Terra, GPT-6 Astra/Sol/Luna and GPT-5.6 Sol/Terra/Luna now reach the picker. Default and verified model remain GPT-5.6 Luna. [Regression report](../test-results/codex-catalog-fix.xml).
- **Learning / remaining limit:** A compatibility fallback is not a complete current catalog. Refresh must reconcile the selectable projection as well as discovery, while retaining owner policy. Listing a model is not proof that inference succeeds; newly selected models still require their explicit test.

#### C73 — Generated account names exposed identifiers instead of recognizable identity

- **Date/status:** November 2, 2026. Fixed and deployed locally.
- **Cause:** connection creation used the first eight UUID characters as a display name; verified identity was shown separately without updating that name. There was no distinction between generated and owner-chosen names.
- **Correction:** new pending connections use “Codex connection”; verified email produces “Codex · email”. Numeric suffixes resolve collisions within the existing 80-character limit. A persisted ownership flag preserves custom names across status checks, reconnects and startup. Migration recognizes only the exact previous generated name, once; API-key names remain untouched. Automatic metadata/name changes do not revise model configuration or reassign bots. Concurrent creation and owner edits share a naming lock.
- **UI / verified metadata:** the editable name follows automatic updates unless it contains unsaved edits. Plan information is displayed separately, only from previously verified identity claims. Long identities wrap on mobile. Workspace names are unavailable in the verified metadata; duplicates therefore use numbers rather than invented organization labels.
- **Verification:** [full isolated suite](../test-results/provider-names-backend.xml): 733 passed; six naming regressions cover migration replay, custom edits, concurrent collisions and plan extraction. Python lint and production build pass. [Synthetic Chrome checks](../test-results/provider-names-ui/report.json) pass at 1440/768/390/320 px, including unsaved-edit preservation and long-email overflow. [Design inspection](../test-results/provider-names-inspect/report.json): zero findings with the existing dialog allowances. Live metadata refresh confirmed the readable name and verified Team label without inference; API name, provider revision, default and eight-model catalog remained unchanged. [Before](../test-results/provider-names-before.json) and [after](../test-results/provider-names-after.json) owner-data checksums match.
- **Learning:** keep display identity separate from immutable IDs, configuration revisions and credentials; distinguish automatic defaults from explicit user choices before refreshing metadata.

#### C74 — Worker overrode the applied named provider with bare “custom”

- **Date/status:** November 3, 2026. Fixed and deployed; 737 backend tests and the disposable native gateway regression pass.
- **Symptom / confirmed cause:** Quill failed before inference with “provider 'custom' resolved without credentials”. Its actual runtime configuration correctly used `custom:hermbot-chatgpt`, the restricted inference endpoint and Codex Responses transport. `worker.claim_new` nevertheless persisted `provider: custom` in every run request. The native API treats this as an explicit override and resolves that unrelated generic provider, losing the named entry's key environment binding.
- **Correction:** omit the provider override from run submission. The already-applied, revision-checked per-bot runtime configuration owns provider identity, endpoint and transport for API-key and subscription connections. Keep model selection and durable request replay unchanged. No Hermes changes or credential copying.
- **Verification / test gap:** worker tests cover all three auth kinds and reject provider/key/endpoint fields in run bodies. The disposable native fixture now exercises the actual gateway selection method, reproduces the exact old failure, then passes the corrected selection plus terminal tool/result, history, image and auxiliary calls. Previous fixtures constructed AIAgent directly, bypassing the worker/gateway integration; additionally, a loopback URL triggers a native no-key bypass that masks the deployed hostname failure. The negative control therefore uses the actual service-URL shape without making any external request. [Native report](../test-results/runtime-provider-native.log).
- **Learning:** test the product-to-harness invocation boundary, including deployed URL shapes; a successful low-level agent fixture does not prove the actual request path. Avoid duplicating configuration ownership in per-run overrides.

#### C75 — Interpretation hid the model-test prerequisite after reconnect

- **Date/status:** November 3, 2026. Fixed and deployed. One minimal real gpt-6-luna test passed, restoring the selected model prerequisite; the saved failed interpretation remains available to retry.
- **Symptom / confirmed cause:** Quill's next message remained queued with failed interpretation and a generic ChatGPT request error. The connected provider had authorization epoch 3 and no verified models. A diagnostic request confirmed HTTP 409 “Test this model in Providers before using the connection”; inference was not submitted. Reconnect correctly clears model verification, but the internal prerequisite was prose while model_client accepts only allowlisted error codes, so its exact recovery step was discarded.
- **Correction:** emit the stable `model_test_required` code and map it to the exact action: select the teammate's model in Providers, click Test model, then retry interpretation. Preserve the model-test requirement and saved owner input; do not silently send task data, restore stale verification or fall back to API billing.
- **Verification:** internal endpoint regression rejects the untested model before the broker and verifies safe, actionable recovery text. The existing unknown-error test still excludes arbitrary provider bodies and tokens. The failed saved task is not automatically replayed.
- **Learning:** prerequisite failures need typed, safe recovery codes across service boundaries; generic transport text makes a correct guard look like an authentication or model outage.

#### C76 — Conversation chronology mixed messages with unanchored runtime footers

- **Date/status:** November 3, 2026. Fixed and deployed locally after the owner's diagnosis/research/implementation request. This is a specifically authorized correction; it does not start the deferred evaluation or unrelated fix backlog.
- **Confirmed scope:** the retained-history scan covered 130 conversations, 950 messages, 572 tasks and 744 attempts. It found no message timestamp reversal or corrupt reply parent. Seven failed response tasks all preceded newer answers; four were outside the global 300-task snapshot. Twenty-one cancelled response executions had no visible result. Forty answered question prompts had no durable question message. Four peer-origin outputs looked like ordinary replies. Forty-six late answers across fourteen conversations require causal context; lateness alone is not corruption. The 77 routine/event system anchors are valid and remain intact. [Sanitized diagnosis](../test-results/chat-ordering-diagnosis.json).
- **Root causes:** App rendered messages, task previews, questions, reviews and old failures as separate lists, then appended task lists after the transcript. Its historical failure lookup depended on a bounded global workspace snapshot. Its two independent message arrays could lose the first row of a sliding live page after older history was loaded; Scout's retained 149-message main chat demonstrated the missing-message-403 case. Task-keyed text accumulation had no durable attempt identity or reconnect reconciliation. Timestamps showed the hour without a day boundary. Unconditional auto-scroll moved a reader away from older content.
- **Correction:** a thin reference/signature index publishes stable entries with immutable positions and increasing revisions under a per-conversation transaction-final counter. Trigger-staged changes roll back with savepoints; business/domain locks precede publication locks, conversation locks are sorted, and no network/model work occurs under publication locks. Existing messages and native/runtime records remain authoritative. The owner API reads a repeatable snapshot, pages by position, and catches up by revision; SSE invalidates and polling repairs missed invalidations. Streaming text is deduplicated at event ingestion and keyed by attempt, with the final saved message reusing that entry. No raw tool/thinking payload is added to the transcript.
- **Presentation:** request state and controls stay under the source owner message; responses show causal references, peer/delegated origin is labelled, and answered/closed questions and reviews remain in chronological history. Current action eligibility is refreshed separately from historical entry revisions, while write endpoints still enforce permissions. One normalized cache retains loaded history, drains bursts over 100 changes, rejects stale revisions and remembers tombstones and unloaded updates. History loading preserves the visible anchor; new activity offers a jump button while reading. Day separators, full timestamp tooltips and quiet initial screen-reader history replace ambiguous hour-only chronology.
- **Verification:** 749 backend tests pass, including concurrent commit ordering, rollback/savepoints, live-page retention, stream/final identity, thread moves, API authorization and durable review closure. Eight synthetic Chrome scenarios pass, including concurrent older-page/live updates, stale-page/tombstone protection and focus preservation. The clean private-copy migration rehearsal is idempotent, preserves all domain-history hashes, recovers 15 legacy answers and classifies the four peer outputs. The deployed owner API covers all 950 messages through 210 pages spanning 130 conversations and 79 thread scopes, with zero missing/unexpected entries. Live cutover hashes preserve messages/tasks/attempts/questions/reviews and 20 bot configurations plus two teaching records. A write-blocked Chrome check against the deployed app confirms Quill's old failures are anchored under their original messages, all 149 Scout root messages load, message 403 remains visible, and mobile has no horizontal overflow; no browser exceptions or writes occurred. The disposable private database copy and dump were removed after verification. [Tests](../test-results/timeline-pytest.txt), [browser checks](../test-results/timeline-ui/checks.json), [migration](../test-results/timeline-migration-rehearsal.json), [live coverage](../test-results/timeline-live-coverage.json), [live browser](../test-results/timeline-live-browser.json).
- **Review/limits:** Docker frontend build and TypeScript checks pass; no new dependency or Hermes change. The visual inspector has no failures after allowing the existing brand mark, actual reactions and stable teammate avatars; its remaining type-density warning is existing 11–12px metadata, with request-state labels/controls at 13px and mobile controls at least 44px. The existing DM Sans/palette and shell are retained (framed-sheet direction, seed 1871768095). Read-only sampled history queries took roughly 16–25ms on this machine; this is not a large-scale load test. Historical model errors and redundant acknowledgement text are preserved. No external provider action or saved task was replayed.
- **Learning:** chronology, causality and current execution state need separate, explicit identities in one read model. A global dashboard snapshot and arrival-time footer rendering cannot serve as conversation history. Test interleaving, rollback, commit order and reconnect behavior as contracts, including already loaded history.

#### C77 — Owner answers escaped threads and launched redundant acknowledgements

- **Date/status:** November 3, 2026. Fixed and deployed with C76.
- **Confirmed causes:** the application-created `question-answer:` intake omitted `reply_to_message_id`; two retained acceptance answers consequently escaped threads 895 and 916. Answer routing resumed the origin but also queued a separate native run just to acknowledge it. A question without a work item serialized its absent target as the string `"None"` in both handoff and routing results, which could reach a UUID field during intake.
- **Correction:** inherit the original question's thread; persist the exact answer-message link; mark an interpreted answer receipt complete without launching an acknowledgement execution. Work updates and additional discussion operations retain their own processing, and an actual clarification can still run. Keep absent targets null. Presentation restores the two proven historical answer scopes and their linked outputs without rewriting saved messages or native sessions. Legacy answers without a message are explicit historical entries, not fabricated executions or owner approvals.
- **Verification:** question/answer thread regression, idempotent receipt processing, a no-work-item answer and answer-plus-clarification regressions pass with existing owner-answer/work-revision tests. The private-copy audit finds four corrected presentation scopes (two answers plus their two linked outputs). Existing approval hashes and authorization checks remain independent of conversational answers.
- **Learning:** record an owner response once, carry scope through every derived application operation, and use deterministic receipts for acknowledgements. An absent domain identity must remain null across service boundaries.

#### C78 — Working Codex inference appears broken because message interpretation exhausts validation

- **Date/status:** November 5, 2026. Investigating the exact saved-response failure; the initial missing-schema diagnosis was corrected during review. The separate C79–C83 defects below are now fixed. No saved task was replayed.
- **Observed evidence:** the Codex provider is connected with plan usage enabled and the configured gpt-6-luna model verified. The host broker and app services are running. The latest Scout input is a two-character greeting. Its November 5 interpretation made twelve successful HTTP 200 internal model-completion calls across three attempts, followed by stored `ValueError` failures rather than authentication/quota/transport errors. Two November 4 Quill inputs have the same stored failure category. A connected-account display alone is not proof of inference health; these successful completion responses are the stronger evidence.
- **Corrected diagnosis:** the full `Interpretation.model_json_schema()` is supplied in `context()['schema']` and serialized into the model's user evidence. The earlier statement that no schema was supplied was wrong: it inspected the request constructor without following its evidence producer. JSON mode does not enforce that schema, but this distinction alone does not establish the exact cause of the saved failures. The earlier offline fixture also omitted the actual evidence schema, so it could not prove a missing-schema defect.
- **Offline verification:** a new probe obtains actual application context and confirms exact equality with the generated routing schema. Separately, a malformed reply still exhausts four correction rounds with the schema present, while a correctly shaped reply succeeds. Those are synthetic reply fixtures, not recovered provider responses. Review reproduced five concrete defects below (C79–C83) with eight isolated probes. OpenAI's [Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs) distinguishes JSON syntax from schema adherence, without proving this incident's rejected fields.
- **Remaining confirmation:** the historical replies/last validation categories were not retained, so the precise cause of the greeting's `ValueError` remains unconfirmed. Preserve application-side authorization/semantic validation and record safe failure categories instead of private model bodies. Automatic approval review rejected the prior saved-context and synthetic probes carrying internal routing instructions because their external prompt egress lacked explicit approval. Neither probe ran; this review used mocked completions and the isolated test database. No credentials were exposed or rotated and no saved task was resumed.
- **Learning:** trace the complete constructed payload before diagnosing a missing contract. Distinguish authentication, inference transport, output structure, semantic routing and application failure. A successful transport plus a generic `ValueError` is insufficient to infer the exact bad output.

#### C79 — An answer can redirect a separate assignment or cancellation onto the wrong work

- **Date/status:** November 5, 2026. Fixed in shared application intake; verified with isolated regression tests. High priority because correctly interpreted cancellation could stop the wrong work.
- **Cause:** `inputs.answer` supplies `owner_work_target` as question provenance, but `work_intake.apply` treats it as an unconditional destination for every assignment/update/cancel operation. It rewrites independent assignments to updates and overwrites explicit cancellation targets. The same metadata is also used by scoped steering/edit endpoints, so a correction must distinguish those explicit UI targets from a mixed conversational answer.
- **Reproduction:** answer an original report's question with “Use Markdown. Separately, create a new budget spreadsheet,” then provide the correct two-operation interpretation. Only one work item remains and the budget requirement is added to the original report. A second probe answers the report's question while correctly requesting cancellation of a separate budget task; the original report is cancelled and the named budget task remains queued.
- **Proposed correction / lesson:** retain explicit independent operations and their identified targets. Scope an enforced UI edit/steer target to the operation it actually authorizes; do not turn question provenance into authority to rewrite every operation. [Reproduction source](../test-results/codex_intake_review_repro.py), [eight-probe report](../test-results/codex-intake-review-2026-11-05.txt). No model or external service was called.
- **Implemented correction:** question-answer provenance no longer rewrites work operations. A scoped UI edit defaults only the first work operation when it is an unidentified assignment; explicit update/cancel targets and later independent assignments are retained. The interpreter receives matching guidance. Retargeting uses copied operations, preserving the caller's interpretation and existing transaction/revision guards.

#### C80 — Invalid context-lookup arguments bypass the interpretation repair loop

- **Date/status:** November 5, 2026. Fixed in shared application intake; verified with isolated regression tests.
- **Cause:** final interpretation JSON validation is caught and fed back to the model, but `ContextLookup.model_validate_json` runs outside that handler. An invalid tool argument therefore aborts the entire interpretation, even when a subsequent model turn could correct it. The outer worker starts a fresh attempt without the failed tool call or its validation feedback.
- **Reproduction:** a mocked first `read_context` call supplies a nonnumeric `before_message_id`, with a valid final response prepared for the next turn. The interpreter raises `ValidationError` after exactly one call and never reaches that valid second response.
- **Proposed correction / lesson:** validate lookup arguments at the tool boundary and return a safe validation result for that exact tool-call ID within the existing bounded repair cycle. Preserve the unavailable-context/permission checks; malformed arguments must not become permission to broaden a lookup. Use the C79 reproduction source/report.
- **Implemented correction:** invalid JSON, cursors and work IDs produce a sanitized tool result for the matching call ID inside the existing four-round loop. Invalid arguments never invoke lookup; a subsequent valid call or final interpretation can succeed. Unsupported tools and missing call IDs fail safely. Both API-key and subscription transports use this same path.

#### C81 — Required-result validation happens after model correction has ended

- **Date/status:** November 5, 2026. Fixed in shared application intake; verified with isolated regression tests.
- **Cause:** `InterpretationOperation` accepts a new assignment with no requirements, and `interpret` returns it as valid. `apply` later rejects it with HTTP 422, outside the correction loop. The worker's generic retry starts interpretation again without explaining that rejection.
- **Reproduction:** a syntactically valid assignment containing only intent and title passes Pydantic interpretation validation, then fails the application's “concrete required results” guard. The transaction rolls back correctly, so the defect is lost repair feedback and unnecessary failed retries, not a partially committed assignment.
- **Proposed correction / lesson:** validate this deterministic assignment requirement before accepting the interpretation, and keep application guards at commit. Feed back only safe, bounded validation categories when further application validation needs correction. Use the C79 reproduction source/report.
- **Implemented correction:** the interpretation model rejects a new assignment without concrete required results, and its schema describes this requirement. The normal correction loop now supplies that feedback before application; the commit guard remains. This closes this deterministic case only, not every application-repair case in M24.

#### C82 — Two terminal refresh failures leave the provider shown as connected

- **Date/status:** November 5, 2026. Fixed across the broker and backend with synthetic provider verification. Not observed as the cause of the saved greeting incident.
- **Cause:** `CodexAccounts.access` recognizes `invalid_refresh_token` and `token_expired`, clears its unusable credentials and requires sign-in. `backend.model_auth.record_failure` omits those two codes from the matching reauthentication cases. `model_client.MESSAGES` also does not map them to the reconnect action. The UI does not continuously poll a connected provider's broker status.
- **Reproduction:** feed either terminal code through the normal backend failure recorder for a connected synthetic provider. It remains connected with plan usage enabled, and the displayed completion error omits the reconnect instruction. A later explicit status check or a second `sign_in_required` failure can repair the displayed state, but the first failure is misleading.
- **Proposed correction / lesson:** align terminal refresh categories and safe recovery messages across the broker, backend and UI; preserve the authorization-epoch guard and keep temporary errors distinct. Use the C79 reproduction source/report.
- **Implemented correction:** one shared set of six terminal refresh codes is used by both broker account implementations, backend failure recording and completion recovery messages. These failures set `reauth_required`, disable plan usage and tell the owner to reconnect through the existing Providers UI. Safe connection errors retain their normalized code. A stale-epoch failure cannot disconnect a newer login. No healthy live connection or credentials were changed.

#### C83 — Interpretation exhaustion loses the actual failure category

- **Date/status:** November 5, 2026. Fixed for future interpretation failures; historical discarded details cannot be reconstructed.
- **Cause:** exhausting the four-round loop always raises the same “more context” `ValueError`, including when all rounds were invalid final JSON and no lookup occurred. The outer worker records the exception class and an HTTP status, but no safe final validation stage/category. Schema failure, lookup exhaustion, semantic rejection and other `ValueError` conditions collapse into the same user-facing interpretation failure.
- **Reproduction:** four mocked schema-invalid final responses, with the real routing schema present and no tools offered, produce a final error claiming more context is needed. Automatic outer attempts can repeat up to twelve model calls without retaining the actual last validation category. The existing saved incident has twelve successful completion requests and generic `ValueError` events, but that alone does not identify which branch exhausted.
- **Implemented correction / lesson:** typed errors distinguish invalid final output, invalid lookup arguments, lookup exhaustion, delivery alignment, refusal, unsupported lookup, oversized responses and missing provider configuration. Events retain the failure stage, normalized code, bounded schema field paths/error types and separate provider/application HTTP status. Unknown field names, values, raw replies and exception prose are excluded from durable diagnostics. Exhausting bounded model correction stops intake immediately instead of restarting the same four-round cycle up to three times; transient/unexpected failures retain the existing outer retry policy. General application-rejection subcategories and repairs remain part of M24/M25, which are not closed by this change.

**C79–C83 verification:** [regression tests](../tests/test_intake_recovery.py) cover mixed answers, cancellation targets, scoped edits, idempotence, lookup repair on both transports, early assignment validation, cause classification, privacy canaries, rollback, reconnect recovery and stale epochs. The [full backend suite](../test-results/intake-recovery-pytest.txt) passed **777 tests** (28 new cases); [lint](../test-results/intake-recovery-ruff.txt) and the [application build](../test-results/intake-recovery-build.txt) passed. The earlier eight-probe artifact deliberately asserts the old defects and remains historical evidence, not a passing regression suite. No live model probes or saved-task replays were performed; Hermes source was not changed.

**Local deployment:** the API, execution/event/notification workers and supervisor now use the fixed image; the idle host broker was restarted for the shared error categories. [Health and running-source hashes](../test-results/intake-recovery-deployment.json) confirm API health, broker availability and the unchanged connected Codex provider. [History before](../test-results/intake-recovery-history-before.json)/[after](../test-results/intake-recovery-history-after.json) and [configuration before](../test-results/intake-recovery-integrity-before.json)/[after](../test-results/intake-recovery-integrity-after.json) match exactly: 982 messages, 591 tasks, 759 attempts, 63 reviews, 43 input requests, 130 conversations, 20 bot configurations and two teaching records. Bot runtimes/volumes and credentials were not changed. Existing failed tasks remain available for an explicit owner retry; this deployment does not replay them.

## Detailed records and maintenance

### How to maintain this record

For each investigation, implementation and review:

1. Find an existing issue or assign a stable ID. Record the date, affected version/build where known, user-visible symptom, expected behavior and sanitized reproduction/evidence references.
2. Separate the immediate trigger, confirmed root cause, contributing design assumption and unknowns. A model mistake or a correctly rejected operation is not automatically a backend defect. Distinguish permissions, unsupported capabilities, invalid references and external constraints.
3. Record the proposed approach and why. When selected, record the decision, meaningful alternatives rejected and tradeoffs. A proposal in this register is not implementation authorization.
4. Record actual changed files/migrations, deployment scope/date and relevant build or commit identifier. Do not invent a commit ID where none exists. Preserve original failures and link superseding corrections.
5. Close only with evidence: a regression/reproduction, relevant integration or real-user acceptance, and explicit remaining limits. Passing synthetic or read-only tests does not establish real external-write acceptance. State what prior tests missed.
6. If the problem recurs, reopen the issue or link the distinct failure mechanism. Update the roadmap/checklist when the finding invalidates a current claim. End the work report with issue IDs and accurate status.

Capture design/architecture, intent/context, state/lifecycle, concurrency, coordination, tools/integration, artifacts/completion, UX/accessibility, security/privacy, operations/performance, observability, testing and external constraints. Capture user reports, near misses caught by guards, failed approaches and review findings as well as thrown exceptions. Do not add an incident for every duplicate log line.

Keep credentials, raw sensitive transcripts, private provider payloads and unnecessary personal data out of this file and diagnostic fixtures. Link scoped local evidence or message IDs instead. Use sanitized application error codes/details; do not solve missing diagnostics by retaining every raw model response.

Statuses: **Open** (unresolved); **Investigating** (cause incomplete); **Implemented, verification pending**; **Verified within stated scope**; **External constraint**; **Corrected finding** (previous diagnosis withdrawn or narrowed); **Deferred** (explicit decision with reason). Confidence is separate from status. No defect is closed merely because its cause is understood.

New-entry template:

```text
ID / title / dates / category / status / impact
Observed and expected behavior; reproduction and affected build
Cause and confidence; original assumption; unknowns
Approach: proposed or selected, rationale and meaningful alternatives
Fix: actual changes and deployment, or explicitly none
Verification: evidence, coverage gap, closure criteria and remaining limits
Related issues; dated corrections/recurrence history
```

### Existing historical records

These remain the detailed source records for earlier findings and corrections. Their dates and test counts describe those builds, not today's entire application. All 85 finding rows from these six reviews, along with the original usage reports and fixes, are individually indexed in [the chronological history](ISSUES.md). Original transcripts have now supplied the diagnoses formerly treated as missing. Unverified historical fixes and uncertain within-review discovery dates remain explicit.

- [Phase 2 review](PHASE2-REVIEW.md), [Phase 3 review](PHASE3-REVIEW.md), [Phase 4 review](PHASE4-REVIEW.md).
- [UI review](UI-REVIEW.md), [skills/tools review](SKILLS-TOOLS-REVIEW.md), [work outcomes review](WORK-OUTCOMES-REVIEW.md).
- [Milestone checklist](CHECKLIST.md): delivery scope and incomplete acceptance gates.
- [Context](CONTEXT.md): original decisions and boundaries, not proof of current behavior.

#### H01 — Tool admission/start-order circular wait

- **Status:** Verified within stated scope, 2026-10-13. **Category:** concurrency/integration. **Impact:** simple parallel tools waited approximately 120 seconds each.
- **Cause:** Hermbot held an exclusive per-bot lock around the Hermes callback. A later callback could hold it while waiting in Hermes's start-order gate for an earlier callback blocked on the same lock. The original takeover design assumed that exclusive foreground-tool admission was compatible with harness parallel execution.
- **Selected approach and fix:** shared admission for each bot callback; takeover closes new admission and drains admitted callbacks exclusively before human input. Preserve viewer/epoch checks and release admission correctly on exceptions/process exit. Hermes source remains unchanged.
- **Evidence:** [diagnosis](../test-results/tool-lock-diagnosis.json), [review and correction](../test-results/tool-gate-review.json), [protocol](../test-results/tool-gate-protocol.json), [live verification](../test-results/tool-gate-live.json). The recorded correction passed 413 backend tests plus parallel/takeover and UI checks.
- **Learning/limits:** previous tests covered a single foreground tool and synchronous callbacks, not reversed entry into parallel tools. This correction does not explain every slow tool/model operation or promise control over arbitrary background processes.

#### H02 — Informational messages blocked work; helper output could complete shared work

- **Status:** Verified within stated scope, 2026-10-13; later acceptance gaps remain open as M03/M05/M06/M26. **Category:** coordination/completion.
- **Cause:** informational communication could use assignment/dependency semantics; evidence publication and responsibility for accepting the shared deliverable were insufficiently separated.
- **Selected approach and fix:** nonblocking scoped inboxes, explicit required waits, contribution versus deliverable responsibility, accountable acceptance, and artifact-inspection receipts. Retain provider receipts and reject unsafe replacements of running/uncertain work.
- **Evidence:** [review](../test-results/coordination-review.json), [real three-bot acceptance](../test-results/coordination-native.json), [retained failed acceptance](../test-results/coordination-native-failed.json), [UI](../test-results/coordination-ui.json). Recorded build: 445 backend tests, including 32 new regressions.
- **Learning/limits:** valid contribution ownership and inspection receipts do not establish semantic sufficiency of the delivered product. No live connected-app write was tested by this acceptance. Do not treat this correction as closing the later Manik incident.

#### H03 — Knowledge dialog overflow and overcrowded roster

- **Status:** Verified within stated scope, 2026-10-07. **Category:** UX/layout.
- **Cause:** long option content expanded a dropdown; a generic modal rule overrode intended width. Separate noncollapsible roster sections consumed the available height.
- **Selected approach and fix:** constrain/wrap content and restore responsive dialog sizing; collapsible roster sections with shared scrolling and retained controls.
- **Evidence and limits:** [UI review](UI-REVIEW.md) records the actual browser verification. This is historical verification of those interactions, not proof that all future dialogs are overflow-free.

### Other unresolved findings recovered from the original chat

The original-source verification recovered additional issues that the first register and chronological backfill did not fully represent. Their full causes, historical responses and evidence are in [the chronology](ISSUES.md).

| ID | Remaining issue / verification boundary |
|---|---|
| KAG-02 | Drive search still exposes an unexplained provider filter as generic `query`. The proposed plain-text interface was not implemented. |
| KAG-03 | Latest saved configuration snapshots still contain enabled test skills. Relevance selection was verified; actual cleanup of these original selections was not. No live configuration change was made by the documentation work. |
| KAG-04 / KAG-05 | The original unsupported competition guidance and unverified shortlist were not independently corrected/rerun by later general quality checks. Earlier search durations are not conclusively attributed to the later reproduced lock cycle. |
| STAR-01 / STAR-02 | The historical missing DOCX package and absent Slack handoff were diagnosed; later artifact/handoff features do not prove that original request completed. |
| TEMP-10 | Source counts, skimming and synopsis-based collaboration did not establish exhaustive reading or independent verification. General evaluations do not remove this qualitative boundary. |
| RET-01 / RET-02 | OCR-dependent retrieval and a Crossref 429 were observed. One recovery does not verify universal OCR capability; general rate-limit recovery has no saved closure evidence. |

Do not silently close these findings because a related feature later shipped. Record the chosen correction and appropriate acceptance before updating their status.

### Manik group incident: M01–M28

Observed: 2026-10-13. Diagnosed: 2026-10-13; register created 2026-10-14. Group `d1a426a3-2148-4e4c-b3ab-0811f3ad1f5e`, messages 544–624. Inspection covered application state and code plus retained Hermes tool calls. It did not replay external actions or alter production data. No fixes for these findings were implemented by the diagnosis or this documentation change.

The following approaches are **proposals**, not a completed implementation plan. Each entry's closure check is future work unless explicitly identified as existing diagnostic evidence. The [six diagnostic probes](../test-results/intake-diagnosis-probes.py) assert current defects rather than desired behavior; they intentionally live outside the regression suite. Their [saved result](../test-results/intake-diagnosis.xml) is not evidence that the defects are fixed.

#### M01 — New Slack send associated with completed action

- **Status:** Open. **Category:** intent/action identity. **Cause:** the replayed interpreter proposal reused `slack-completion`; [registration](../backend/outcomes.py) correctly rejected rewriting a fulfilled external effect. The prompt describes the distinction, but schema/application feedback does not reliably enforce it. Exact historical rejected proposals are unavailable.
- **Evidence:** [model replay](../test-results/manik-intake-replay-result.json), [isolated apply/control](../test-results/manik-intake-apply-result.json): 409 for old key; changing only the requirement/authorization key succeeds.
- **Proposed approach:** represent another requested send as a distinct action and return application validation feedback for repair while preserving the original receipt and latest-message authorization.
- **Close when:** a follow-up send creates exactly one new requirement; duplicate delivery of that same instruction does not create another; discussion grants no send authority. No historical Slack request is replayed merely to deploy a fix.

#### M02 — Unrequested study planner substituted for blocked work

- **Status:** Open; behavioral cause partly known. **Category:** planning/scope. **Observed:** message 546 described an alternative planner; owner rejected alternatives at 547.
- **Cause:** the bot selected a different output when access was blocked. Local creation/supporting publication does not establish relevance to the requested deliverable. The record does not establish why the model selected this particular alternative, or that backend code instructed it to do so.
- **Proposed approach:** preserve the requested scope and distinguish optional supporting work from satisfying it; report a concrete blocker when the deliverable cannot be produced.
- **Close when:** a blocked-input acceptance case preserves the objective and does not present an unrequested alternative as its replacement.

#### M03 — Outline accepted as website copy

- **Status:** Open. **Category:** completion/scope. **Cause:** the accountable executing bot accepted an outline while explicitly excluding lesson bodies and other requested content. [Acceptance](../backend/result_acceptance.py) requires review text and valid evidence references, not proof of scope satisfaction. Coordination version was 1, so M26 is not its cause.
- **Evidence:** original `website-copy` acceptance at 12:50:39 UTC, artifact `785ee649-c57a-5d04-95db-3b88f0616148`; owner correction at 589.
- **Proposed approach:** state verifiable delivery conditions and explicit scope limitations; require resolution of a material scope reduction before fulfillment. Keep qualitative judgments honest about their evidence strength.
- **Close when:** a list-page replica cannot satisfy a requirement for lesson content; an explicitly owner-accepted partial result can close at its agreed boundary.

#### M04 — ZIP publication unsupported

- **Status:** Open. **Category:** artifacts/capability. **Cause:** `application/zip` is absent from [FILE_TYPES](../backend/library.py); [publish_file](../backend/artifacts.py) rejects it before workspace export.
- **Proposed approach:** support bounded ZIP file delivery through the existing artifact path, without requiring extraction or executing its contents. Preserve file-size, path and integrity checks and accurate tool descriptions.
- **Close when:** a generated ZIP downloads with identical bytes and usable filename; invalid paths and oversized input remain rejected.

#### M05 — Manifest accepted as ZIP delivery

- **Status:** Open. **Category:** completion/contract. **Cause:** [OutcomeSpec](../backend/models.py) records only broad `artifact` kind, and [claim validation](../backend/outcomes.py) accepts an accessible artifact without checking requested format/delivery. The review expressly disclosed that the actual ZIP was local.
- **Evidence:** `zip-results` revision 2 acceptance at 12:52:34 UTC references Markdown artifact `f6c43055-21e8-5628-bd1f-4911b555bf53`.
- **Proposed approach:** distinguish supporting evidence from the deliverable; bind acceptance to the actual downloadable file and relevant contract conditions.
- **Close when:** a manifest alone cannot fulfill ZIP delivery; the published archive can. M04 alone does not fix this semantic gap.

#### M06 — Derived ZIP stayed fulfilled after upstream changes

- **Status:** Open. **Category:** versioning/dependencies. **Cause:** [dependency readiness](../backend/outcomes.py) checks status by key, without recording the upstream accepted revision used to build a derived output. Revising website content did not invalidate the earlier ZIP acceptance.
- **Evidence:** ZIP acceptance retained the six-file manifest while the later extraction described 87 files.
- **Proposed approach:** record derivation/version relationships and mark affected outputs stale when their inputs change. Preserve immutable historical external effects; do not automatically resend Slack because content changed.
- **Close when:** relevant upstream revisions require a refreshed derived deliverable, unrelated revisions do not, and prior external receipts remain intact.

#### M07 — Slack completion linked a private container path

- **Status:** Open. **Category:** delivery/usability. **Cause:** the bot used a local path after ZIP publication failed. A provider receipt validates posting, not recipient access to a mentioned file. M04/M05 allowed this misleading completion state.
- **Evidence:** the only successful original Slack operation was `bd4b208d-a9df-4016-a23b-303f47867f8d`; it succeeded at 12:55:27 UTC. Do not relabel it as a failed send or assume the recipient read it.
- **Proposed approach:** base delivery notifications on the accepted actual deliverable and accurately describe access; a private application link also must not be assumed accessible to a colleague.
- **Close when:** messages requesting file delivery reference a recipient-usable delivery mechanism or explicitly report the remaining delivery blocker.

#### M08 — Published website assets lost their relationships

- **Status:** Open. **Category:** packaging/testing. **Cause:** HTML used local relative filenames while separate published artifacts had different names. Local browser verification tested a different package from the downloaded output.
- **Proposed approach:** deliver a coherent archive or self-contained document using existing publication, and verify the exported package from a clean directory. Website hosting is not implicitly part of this fix.
- **Close when:** the downloaded output's links/assets work without the bot's workspace. Published HTML sandbox restrictions remain explicit.

#### M09 — Descriptive titles used as extensionless filenames

- **Status:** Open. **Category:** artifact UX. **Cause:** [filename validation and download response](../backend/library.py) accept a safe arbitrary name and use it unchanged; display title and usable filename are conflated.
- **Proposed approach:** preserve/validate the actual filename and keep descriptive labeling separate where needed; define behavior for missing or conflicting extensions.
- **Close when:** downloaded HTML/JSON keep suitable names without losing meaningful labels or weakening path/header safety.

#### M10 — Reopen retained cancelled requirements

- **Status:** Open; reproduced. **Category:** lifecycle. **Cause:** [register](../backend/outcomes.py) resets status only when descriptive fields differ. Identical requirements remain cancelled, and the intake contract has no explicit reopening transition.
- **Evidence:** messages 560/566 applied updates with identical requirements; [diagnostic probe](../test-results/intake-diagnosis-probes.py).
- **Proposed approach:** an explicit owner-requested lifecycle transition independent of text edits; define fulfilled/uncertain external-effect handling before implementation.
- **Close when:** stopped unfinished work resumes coherently without renaming requirements, duplicating completed effects or bypassing reconciliation.

#### M11 — Normal confirmations failed strict choice matching

- **Status:** Open; exact comparison reproduced, historical rejection cause partly uncertain. **Category:** intent/input.
- **Cause:** the interpreter asked for an exact choice after “done, move forward”; [answer](../backend/inputs.py) also rejects punctuation variants because it requires literal equality. Ordinary conversational answers and exact action approvals need different semantics.
- **Proposed approach:** associate the actual answer with the identified question and normalize unambiguous ordinary choices while preserving original text; retain exact approval requirements for consequential actions.
- **Close when:** harmless wording/punctuation variations work, ambiguous/negative answers do not become assent, and an expired question stays rejected.

#### M12 — Clarification requested already available course information

- **Status:** Open; erroneous interpretation confirmed, model's internal cause unknown. **Category:** context/intent.
- **Cause:** the saved interpretation at 593 asserted missing course information despite earlier course context remaining inside the history window. M27 promoted that mistake into the executing bot's immediate instruction. This is not proven context-window exhaustion.
- **Proposed approach:** resolve references against recorded work and available conversation facts before asking; preserve uncertainty instead of asserting unsupported absence.
- **Close when:** sanitized replay/evaluation cases reuse known course/scope facts across follow-ups and ask only when needed. One successful model response is insufficient robustness evidence.

#### M13 — Confirmations repeatedly requested

- **Status:** Open. **Category:** question lifecycle/context. **Cause:** conversational clarifications and durable input requests have inconsistent answer tracking. Some scope confirmations were saved correctly but later executions reconsidered them; this is not evidence the database lost them.
- **Proposed approach:** consistently associate questions, answers and resolved scope with the work; reuse answers unless a material change makes them insufficient.
- **Close when:** equivalent follow-ups do not repeat settled questions, while a changed scope can legitimately require a new answer.

#### M14 — Delayed acknowledgement answered a newer question

- **Status:** Open; mechanism reproduced. **Category:** context/ordering. **Cause:** [answer handling](../backend/inputs.py) resumes the origin and separately queues acknowledgement; question context is task-local, and [worker sessions](../backend/worker.py) persist per bot/conversation. The acknowledgement lacks its answered-question receipt while seeing newer session history.
- **Proposed approach:** make acknowledgement refer explicitly to its question/answer and avoid a separate unconstrained agent execution for a routine receipt.
- **Close when:** the original task can ask a second question immediately without changing what the first answer's acknowledgement means.

#### M15 — Duplicate acknowledgements queued behind long work

- **Status:** Open. **Category:** scheduling/UX. **Cause:** answer-plus-update produces multiple executions in [intake](../backend/work_intake.py). One active attempt per bot and oldest-task-first [scheduling](../backend/worker.py) let the resumed original work finish before acknowledgements. Message 605 received responses approximately 42 minutes later.
- **Proposed approach:** apply the answer/update once and produce one appropriate acknowledgement without competing execution jobs. Preserve one active bot execution; raising concurrency alone is not a solution.
- **Close when:** a long resumed task cannot delay or duplicate the answer receipt; actual work is still resumed once.

#### M16 — Owner-facing clarification sent to a teammate

- **Status:** Open; bot audience-selection error confirmed. **Category:** communication/identity. **Cause:** Quill chose peer messaging and described Scout as “researcher/owner.” [Coordination](../backend/coordination.py) authorizes peers correctly but does not establish that the owner received an answer.
- **Proposed approach:** carry explicit response audience/provenance and distinguish peer consultation from answering the user. Avoid disabling legitimate discussion-time peer consultation.
- **Close when:** owner clarification/status receives a direct response and peer replies cannot acquire owner authority.

#### M17 — Failed intake became “owner did not authorize”

- **Status:** Open. **Category:** state/context/authorization. **Cause:** failed newer requests created no work changes/authorization, and the later peer task was unlinked to work. [Execution context](../backend/work_items.py) exposed processed instructions; the bot treated absence of recorded authority as absence of an owner request.
- **Proposed approach:** surface a pending/failed interpretation distinctly from a missing request. Repair intake before execution rather than treating raw failed proposals as permission.
- **Close when:** a status/peer response reports processing failure accurately and neither invents authority nor asks the owner to repeat an already clear instruction unnecessarily.

#### M18 — Mistyped internal reply ID escalated to owner

- **Status:** Open; exact trigger confirmed from Hermes history. **Category:** references/recovery.
- **Cause:** actual message ID ended `60d9ece3`; the bot submitted `60e9ece3`. [Reply validation](../backend/coordination.py) correctly rejected it with a generic 403 also used for scope failures. Recovery was incorrectly delegated to the owner.
- **Proposed approach:** select exact scoped references from the inbox and return actionable, privacy-preserving reference feedback; never ask the user to repair internal UUIDs.
- **Close when:** an invalid reference is recoverable using available inbox data, while cross-scope references remain rejected.

#### M19 — Help request got only an acknowledgement

- **Status:** Open. **Category:** interaction/lifecycle. **Cause:** the “Need help” choice resumed work still affected by M10 and created an acknowledgement-only turn. [Routing](../backend/work_items.py) told the latter to acknowledge and finish; it could do so without providing the requested instructions.
- **Proposed approach:** treat a request for help as substantive content to answer, associated with the original obstacle, rather than only an answer receipt.
- **Close when:** choosing help produces usable instructions and preserves work state without pretending the blocker is resolved.

#### M20 — Interactive vault prompt unavailable in API execution

- **Status:** Open integration/recovery gap; harness limitation confirmed. **Category:** capability/input.
- **Cause:** upstream `browser_vault_save_login()` returns `prompt_unavailable` without an interactive callback. Hermbot exposed the native tool in its headless/API execution; repeated calls could not create that surface. Hermes correctly reported the limitation.
- **Proposed approach:** supply accurate runtime guidance and use a supported secure-input path or supported callback integration. Keep credentials outside chat and Hermes source unchanged; no blanket native-tool blocking is authorized.
- **Close when:** the headless login workflow guides the owner through a functioning secure path and avoids repeatedly calling an unavailable prompt.

#### M21 — Slack write attempted before required ZIP was ready

- **Status:** Open orchestration behavior; protective guard worked. **Category:** dependencies.
- **Cause:** delegated Slack task had no task dependencies and started immediately, while outcome dependencies were enforced at dispatch. The bot attempted the write rather than preparing then waiting. [Task delegation](../backend/service.py) and [outcome readiness](../backend/outcomes.py) express different dependencies.
- **Proposed approach:** make prerequisite readiness explicit to the responsible execution and support preparation followed by an actual dependency wait. Do not require all preparatory work to wait unnecessarily.
- **Close when:** delivery happens only after accepted prerequisites, without premature failed dispatch or polling; the backend guard remains.

#### M22 — Earlier broad “poor revision recovery” finding corrected

- **Status:** Corrected finding, 2026-10-13 investigation. **Category:** diagnosis/access.
- **Correction:** revision rejections occurred in one batch over about 35 ms; status read succeeded roughly eight seconds later. This does not demonstrate a persistent retry loop. One denied artifact existed in previous work but was not granted to its replacement; another supplied artifact ID did not exist.
- **Evidence:** affected Hermes session `4dbdb6c1-ab4d-4b72-949e-a38fc5be55d8`, tools around 12:55:55 UTC; artifact `d58bc4c8-4455-5229-a0de-7834f9997f31` had no replacement-task grant; `7869d9e6-8db7-54f4-a46c-2f7ade37b0da` did not exist. No raw session transcript is copied here.
- **Approach:** preserve correct revision/access guards; evaluate recovery per actual cause. Reference usability belongs with M18/M23. No fix is claimed or required for the alleged persistent loop without new evidence.

#### M23 — Invalid tool schema and contribution references

- **Status:** Open reliability issue; validators worked. **Category:** tool use/contracts.
- **Cause:** action arguments supplied to discovery-only `hermbot_connectors`; required `result` omitted from finish; old root tasks supplied as current contribution IDs. These were observed invalid calls, not proof that [published schemas](../plugins/hermbot/__init__.py) were wrong.
- **Proposed approach:** use exact discovered references and clear argument/recovery contracts; review duplicated schema definitions for drift before attributing errors to them.
- **Close when:** representative invalid calls get useful corrective feedback and recover with valid inputs; unrelated or stale contribution IDs remain rejected.

#### M24 — Retry lacked application rejection feedback

- **Status:** Open; code confirmed. **Category:** interpretation/recovery.
- **Cause:** schema feedback occurs inside `interpret()`, but application validation in `apply()` runs afterward. Its exception starts another attempt without the rejected proposal or precise rejection feedback. See [intake](../backend/work_intake.py).
- **Proposed approach:** return structured application validation to a bounded repair flow against fresh state; distinguish transient conflicts from invalid proposals and preserve transaction/authorization guarantees.
- **Close when:** M01-style rejection is repaired or reported specifically, without repeated identical blind attempts, partial application or duplicate effects.

#### M25 — Intake failure details discarded

- **Status:** Open. **Category:** observability/privacy. **Cause:** the [exception handler](../backend/work_intake.py) avoids storing sensitive provider responses but also drops safe application failure details. Recorded 33 retries and 12 failures expose `HTTPException` without its cause.
- **Proposed approach:** retain structured sanitized error codes, failure stage and correlation/revision metadata sufficient to diagnose and repair; preserve privacy by design, not by logging complete proposals/transcripts.
- **Close when:** immutable action, invalid ordinary answer and stale-state failures are distinguishable; diagnostic output is tested against secret leakage.

#### M26 — New executions silently inherited legacy semantics

- **Status:** Open; reproduced and confirmed in production metadata. **Category:** versioning/construction.
- **Cause:** [create_task](../backend/service.py) defaults coordination version to zero; [derived intake](../backend/work_intake.py) and [UI-answer creation](../backend/inputs.py) omit it. A compatibility rule can propagate the downgrade. This is not the cause of M03/M05, whose acceptances used version 1.
- **Proposed approach:** make current/inherited execution semantics explicit at every constructor; confine legacy behavior to genuine legacy work without silently rewriting historical evidence.
- **Close when:** UI answers and multi-operation intake use intended semantics, existing legacy work remains safe, and mixed-version continuation is covered.

#### M27 — Clarification replaced immediate owner input

- **Status:** Open; reproduced. **Category:** provenance/context.
- **Cause:** [intake](../backend/work_intake.py) stores clarification in `continuation`; [worker](../backend/worker.py) uses `continuation or input`. Original text remains stored but the immediate request becomes an instruction to ask the interpreter's question.
- **Proposed approach:** preserve the authenticated user message and represent interpretation/clarification separately with its provenance; avoid having a second model blindly execute a mistaken clarification as the user's request.
- **Close when:** the original request remains available and accurately distinguished from application guidance, including M12-style erroneous clarification.

#### M28 — Chat credential copied into execution records

- **Status:** Open. **Category:** security/privacy. **Cause:** ordinary [task creation](../backend/service.py) stores the submitted message as task input, and [execution](../backend/worker.py) copies it into attempts. Secure input protects its separate path, not credentials pasted into ordinary chat.
- **Evidence:** prior bounded scan found the known posted password in four messages, four task inputs and two attempt inputs, and in no published artifacts. This is not a comprehensive proof of absence from every log, model request or native session. No secret is included here.
- **Proposed approach:** define handling for accidental chat secrets, minimize downstream propagation, and provide a secure replacement path. Existing-record cleanup and credential rotation require a concrete scoped plan; this documentation does not perform them.
- **Close when:** secret canaries do not propagate into ordinary execution/diagnostic outputs through the corrected path and historical exposure/remaining limitations are explicitly accounted for.

### External constraints and actions that were correct

- CAPTCHA needed human assistance; the trial allowance was exhausted. Neither is a demonstrated application defect. The later partial result was accepted after the owner's explicit instruction (message 622), unlike the earlier implicit scope reduction in M03.
- Slack history readback lacked a scope, but the first send had a valid success receipt. Readback failure does not invalidate that receipt or authorize resending.
- README removal was an authenticated owner UI action, not silent agent deletion of a requirement.
- Invalid reply IDs, unshared artifacts, stale revisions, unfinished delivery dependencies and disabled skills were correctly rejected in the observed cases. Improvements must preserve their protections.

### Learning priorities and closure boundaries

1. Preserve the meaning and identity of owner requests through interpretation, application, continuation and communication; repair failures with precise feedback (M01, M10–M19, M24–M27).
2. Verify the delivered product, not merely the presence of evidence; preserve derivation and recipient access (M02–M09).
3. Match integration behavior to actual runtime capabilities and current references (M18, M20–M23); do not modify Hermes source to hide application integration mistakes.
4. Treat privacy-safe diagnosability and realistic multi-turn acceptance as part of correctness (M25, M28). Test interactions between features, not only successful isolated APIs.

This register, [the chronological history](ISSUES.md) and [the original-turn coverage check](CHAT-COVERAGE.md) preserve all 28 findings from this incident, all findings in the six saved reviews and all reported issues recoverable from the three scoped original transcripts. The original chat verification corrected the earlier mistaken assumption that older diagnoses were unavailable. Future work must update the relevant chronological entry, supporting coverage and detailed record in this file. Automatic ingestion and a new monitoring system have not been implemented. Complete coverage of these recorded conversations is not proof that every possible application defect is known or fixed.
