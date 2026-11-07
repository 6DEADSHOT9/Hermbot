# Phase 6.2 Demonstration Teaching Plan

Recorded October 27, 2026. Implementation authorized and built locally; real owner acceptance remains pending. See [implementation, review and verification](PHASE6-TEACHING-REVIEW.md). The researched scope below remains the acceptance contract. The owner placed Phase 6.1 voice on hold and selected 6.2 next. This proposal preserves Phase 6 numbering, the separate Phase 6A mobile milestone, deferred connected-service coordination, evaluation-before-backlog-fixes ordering, and deferred hosting/VM work.

## 1. Outcome and scope

An owner demonstrates a procedure on a selected bot's existing computer, reviews the captured steps and permitted images, generates an editable skill, tests an exact candidate against new inputs, and explicitly publishes/enables a version. Future executions use the ordinary Hermes agent loop, current tools, permissions, work requirements and result verification.

Use a structured action timeline and selected screen keyframes for the initial release. Continuous video is not required to teach or inspect the procedure. Recording is restricted to the selected bot desktop controlled through Hermbot; it does not record the owner's host desktop, unrelated browser tabs, microphones, or another bot. Browser-native DOM enrichment is optional and requires proof that it observes the same desktop browser/profile/window; never assume the existing native browser tool, Playwright and the desktop share a session.

The published deliverable is a self-contained native `SKILL.md`. Reference material can be summarized into this bounded document. Multi-file executable bundles, general-purpose macro replay, model training and autonomous skill updates are outside this milestone. These choices preserve the complete demonstration-to-reviewed-skill workflow without adding a second execution platform.

The owner may demonstrate real actions. Recording does not make the application being operated a sandbox or undo effects. Live connector tests retain their exact-action review/standing-permission rules. Independently authenticated browser/terminal activity retains its existing connector-review coverage limit.

## 2. Current implementation inspected

| Existing path | Available behavior | Integration consequence |
|---|---|---|
| `backend/computer.py` | Authenticated status/control, admission fence, native takeover/handback, same-origin authenticated WebSocket relay | Reuse ownership and session authorization. Recording needs a separate explicit state, not another desktop owner. |
| `docker/runtime_api.py` computer routes | Private adapter, supported native desktop/lease calls, drain-before-input, viewer/epoch readiness marker, filtered RFB forwarding | Add recording to Hermbot's adapter, preserving the unmodified native gate. |
| Pinned native `tools/bot_desktop/rfb_filter.py` | Stateful message framing; keyboard/pointer/clipboard/resize gating; QEMU extended key events; arbitrary WebSocket chunk boundaries | An observer must handle the accepted protocol, not count WebSocket packets as actions. The current filter exposes allowed bytes, not a semantic recording callback. |
| Pinned native `tools/bot_desktop/thumbnail.py` | Public capture helper uses Pillow and an in-memory JPEG buffer; default thumbnail bound is 960×600 | Reuse the existing capture dependency with an explicitly validated readable size. Record source/frame geometry and do not assume default thumbnail text is sufficient for teaching. |
| `web/src/Computer.tsx` | noVNC view, takeover, secure input, connection status; secure input sends normal key messages | A recorder cannot identify secure input by inspecting those messages. Add a server-acknowledged protected-input transition before typing. |
| `backend/knowledge.py` | Owner drafts, immutable versions, native hash compare-and-swap, busy/human-control guards, explicit enablement and queued runtime refresh | Reuse publication and rollback semantics; add teaching provenance and exact test binding. |
| `docker/runtime_api.py` skill routes | Validated owner-managed `hermbot-*` skills, readback, atomic file replacement and same-content replay | Keep native installation here. Generated names should satisfy the stricter current format limits while existing unrelated skills remain intact. |
| `plugins/hermbot/__init__.py`, `backend/capabilities.py`, `backend/service.py` | Supported execution middleware, enabled-skill filtering, task preparation/capability fingerprints, referenced-skill validation | An unpublished test candidate needs a narrowly scoped integration across these callers. A prompt mentioning a disabled skill is insufficient. |
| Existing completed-work skill draft | Concatenates task inputs/outputs into an editable draft | Keep it available; it is not a recording, action trace, generalization check or tested teaching version. |
| Existing artifact storage/library | Immutable content, hashing, access/share checks; ordinary file-type/size limits | Reuse storage transport and integrity helpers, but keep teaching capture private and outside automatic library indexing/sharing. |

The source inspection and pinned-filter/thumbnail reads were read-only. Current secure input does not itself retain keystrokes; the concern is excluding it from the proposed future recorder. No live demonstration, dependency installation, provider call, native knowledge write or runtime change was performed for planning.

## 3. Research and implementation lessons

| Reference | Inspected mechanism | Application to Hermbot |
|---|---|---|
| [ShowUI-Aloha repository](https://github.com/showlab/ShowUI-Aloha), [parser](https://raw.githubusercontent.com/showlab/ShowUI-Aloha/main/Aloha_Learn/parser.py), [log processor](https://raw.githubusercontent.com/showlab/ShowUI-Aloha/main/Aloha_Learn/log_processor.py), [trace generator](https://raw.githubusercontent.com/showlab/ShowUI-Aloha/main/Aloha_Learn/trace_generator.py) | The source pipeline parses/coalesces event logs, extracts screenshots/crops and produces a semantic trace. Its January 12, 2026 [paper](https://arxiv.org/abs/2601.07181) describes the recorder/learner/planner/executor separation. | Preserve evidence separately from inferred procedure. Merge typing/scroll noise before generation. Reuse Hermes as executor; do not adopt a second agent stack or the reference project's fixed provider/model choices. Windows/macOS claims do not establish Linux-container compatibility. |
| [Chrome Recorder](https://developer.chrome.com/docs/devtools/recorder/reference) | Editable structured steps, multiple selector representations, assertions and replay inspection | Give owners an inspectable step list and explicit success conditions. A recorded coordinate is evidence, not a permanent locator. |
| [Playwright codegen](https://playwright.dev/docs/codegen), [locators](https://playwright.dev/docs/locators), [trace viewer](https://playwright.dev/docs/trace-viewer) | Generated interactions, user-facing locators and per-action snapshots | Capture context and expected state. Do not inject a browser recorder into Hermbot's canvas and claim it recorded the remote application. |
| [Playwright authentication](https://playwright.dev/docs/auth) | Saved browser state can contain impersonation-capable cookies/headers | Keep profiles, cookies, headers and storage state out of teaching artifacts and skills. |
| [Selenium IDE](https://www.selenium.dev/selenium-ide/docs/en/introduction/getting-started) | Explicit record/stop/edit/save/play lifecycle | Separate capture, editing, testing and publication in the user flow. |
| [OpenAdapt record checks](https://docs.openadapt.ai/how-to/record-checks/) | Declared save conditions plus independent record readback; generated check scaffolds still require meaningful configuration | Distinguish an observed click, UI change and verified application result. Define test assertions against actual records/files where available. |
| [OpenAdapt data handling](https://docs.openadapt.ai/security/data-handling/) | Separates local captures, reviewed sanitized derivatives and exact approved upload bytes | Freeze the specific reviewed evidence sent to the generation model. Changes to that selection invalidate the prior approval. |
| [OpenAdapt privacy source documentation](https://github.com/OpenAdaptAI/openadapt-privacy/blob/main/README.md) | Text/image scrubbing and explicit unsupported-image/model failures; documents an OCR miss on light text over a dark title bar | Redaction is defense in depth. Prevent protected-input capture first, require owner review, and do not promise that OCR finds all secrets. |
| [rrweb source guide](https://raw.githubusercontent.com/rrweb-io/rrweb/master/guide.md) | Recording-time exclusion/masking; incremental recordings require coherent snapshot/event chains | Apply exclusions before persistence; retain sequence/gap information. Its DOM classes cannot identify fields inside a remote VNC framebuffer. |
| [WebRobot paper](https://arxiv.org/html/2203.09993v1) | Interactive program synthesis, validation and correction when examples are ambiguous | One example does not establish every branch or loop. Ask for a meaningful correction or additional example rather than inventing behavior. |
| [Agent Skills specification](https://agentskills.io/specification), [Hermes skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) | Native Markdown/frontmatter skill packaging and on-demand consultation | Produce ordinary compatible skills. Published documentation may exceed the pinned runtime; validate against the installed version before using a feature. |
| [August 9, 2026 skill-reusability preprint](https://arxiv.org/abs/2608.08453) | Reports routing, packaging and portability defects in public skills | Include description quality, bounded actionable instructions and fresh-input tests. Its detected defect rates are not execution-success rates or guarantees for Hermbot. |

These are references, not selected dependencies or independent validation of vendor performance. OpenAdapt's [capability page](https://docs.openadapt.ai/reference/capabilities/) distinguishes synthetic demonstrations, planned features and untested real environments; preserve those qualifications when assessing adoption. No new capture framework is selected by this plan.

## 4. Broader software patterns

1. **Event log with visual observations:** keep immutable ordered capture facts, then derive a human-readable procedure. The application does not need to become a general event-sourced system.
2. **Build and promotion pipeline:** source capture, reviewed evidence, draft, test and published version are distinct artifacts. [SLSA provenance](https://slsa.dev/spec/v1.2/provenance) motivates linking inputs and outputs by digest; [deployment protection rules](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments) illustrate checking the specific artifact before activation. No SLSA platform or hosted CI dependency is proposed.
3. **Durable jobs and reconciliation:** PostgreSQL stores transitions; model/storage/native calls occur outside long database locks. [Transactional outbox guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html) explains dual-write failure and duplicate-consumer handling. Reuse existing worker/operation patterns rather than adding a broker.
4. **Stable operation identity:** capture boundaries, generation, tests and publication have independent idempotency keys. [AWS retry guidance](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/) motivates detecting a reused key with changed intent. Retry storage/job delivery; never automatically replay a demonstrated GUI action after uncertainty.
5. **Fencing and explicit ownership:** session generation, desktop epoch and viewer identity prevent a stale tab/collector from appending to a newer recording. A disconnected browser is not an instruction to hand control back.
6. **Recorded dispatch is not proof of outcome:** RFB forwarding and transport receipts establish only their own boundary. Test completion needs current task/result evidence and declared assertions.

## 5. User flow

1. Open the selected bot's Computer and choose Teach a workflow. Enter a goal and expected result; choose the intended app/test context. Show capture scope and retention/model-processing behavior.
2. Take over through the existing control flow. Wait for all admitted native callbacks to settle and for this viewer/epoch to become input-ready. A blocked takeover is not a started recording.
3. Start recording explicitly. Show a persistent recording indicator, elapsed time, retained size and Pause/Finish/Discard. No action is replayed by starting recording.
4. Demonstrate the procedure. Optional notes explain intent and expected results. Protected input requires the acknowledged privacy pause below.
5. Finish capture and drain the accepted prefix. Review the timeline and selected images; remove irrelevant steps, replace private sample values with parameters, mark uncertain/missing steps and define assertions. Finishing does not hand control back automatically.
6. Choose Generate draft after reviewing exactly what will be processed by the selected model provider. Review the resulting prerequisites, parameters, steps, checks, recovery behavior and inference labels.
7. Hand the computer back explicitly before agent execution. Test this draft version on fresh inputs through normal task/grant/review paths. Display actual outputs and failures.
8. Edit/retest as needed, then Publish and optionally Enable for the selected bot. Activation waits for native write/readback and runtime refresh. It does not grant credentials, management authority or permission for future external writes.

A recording list is reachable from the bot's Computer and Skills surfaces so sessions remain recoverable after navigation. The existing Save a completed workflow action remains distinct from Teach a workflow.

## 6. Recording states and invariants

Use separate state fields for desktop ownership, capture, processing and publication. Avoid one status that combines all four.

Capture lifecycle: `preparing -> recording <-> paused -> finalizing -> ready`. Also represent `interrupted`, `discarded` and recoverable `failed`. Privacy pause is an explicit pause reason. Generation/test/publication each have their own queued/running/completed/failed/cancelled state and exact input hash.

- At most one capture session per bot; the active viewer must hold the matching desktop lease epoch. Several bots can have independent sessions within measured resource limits.
- Changing bot, viewer ownership, runtime incarnation or recording generation cannot silently continue the same segment.
- Every retained event has a session ID, generation, segment, monotonically increasing sequence, monotonic capture time, action kind and source geometry. Wall time is display metadata, not the ordering key.
- State transitions use expected revisions and idempotency keys. An identical retry returns its existing outcome; changed bytes under the same event/key are rejected.
- Finish seals the retained prefix only after all acknowledged chunks are persisted. A gap or uncertain tail produces an interrupted/partial recording, never a silently complete one.
- No recording operation executes a captured action again.
- Loss of the viewer, authorization or collector health pauses capture. Human desktop ownership remains until explicit handback. Reconnection needs explicit resume against current state.
- Ending, discarding or deleting recording data does not undo actions already performed in the demonstrated application.

## 7. Capture at the authoritative boundary

Add a Hermbot-owned observer around the existing private adapter's admitted RFB stream. Preserve the native `RfbClientFilter` and drain/lease behavior unchanged. The observer needs accepted keyboard, pointer, drag, wheel and relevant geometry events. It must handle message fragmentation/coalescing, normal and QEMU key events, clipboard extensions and reconnect handshakes. Use a standard parser where its supported scope fits; otherwise a small application-owned observer parses only the known accepted protocol. It is not a replacement security gate.

Associate each accepted input with its admission-time viewer/epoch and recording generation. A later screenshot or current lease read must not relabel an older input. Unknown observer formats pause/mark the capture incomplete while the existing native gate continues to own input authorization; do not add blanket native-tool blocking.

Coalesce pointer movement, key repeats, typing and scroll bursts into editable semantic steps. Preserve key-up/down and drag boundaries internally, then derive concise actions. Input keys are not proof of final field text: IME/composition, layout, backspace, clipboard and shortcuts need explicit handling; unsupported cases are marked rather than guessed. Clipboard recording is off by default; use an owner-supplied parameter/note when omitted data matters.

Capture bounded full-screen keyframes around meaningful transitions and attach capture-time/geometry provenance. Optional crops refer back to their complete parent frame. A frame taken after input is a later observation, not a guaranteed completion acknowledgement. Store native desktop coordinates and image dimensions; CSS-scaled noVNC coordinates are not native coordinates. Resolution changes open a new geometry segment. Avoid duplicate idle frames.

Do not claim browser DOM/ARIA capture from the outer Hermbot page. [noVNC](https://raw.githubusercontent.com/novnc/noVNC/master/docs/API.md) carries framebuffer data through WebSockets; [RFB](https://www.rfc-editor.org/rfc/rfc6143) defines low-level client events, not page elements or user intent.

## 8. Protected input and private evidence

The current secure-input box generates ordinary VNC keys. During teaching, it must first request a private-input pause and wait for the adapter's acknowledgement. The transition must serialize with input observation and frame capture; turning a frontend indicator off is insufficient.

- Before secret entry, stop retaining keyboard/clipboard data and frames, close/discard pending coalescing buffers, and fence in-flight screenshot results with a new privacy generation.
- Sensitive input remains transient on the authenticated live-control path. Store only a generic omitted private-step marker, not the value, keystroke sequence, clipboard payload or secret length.
- Keep capture paused through login/navigation until the owner has a safe screen and explicitly resumes. Reject late images from the previous generation. An old screenshot promise or thumbnail cache must not repopulate retained evidence.
- Provide ordinary Pause for arbitrary sensitive screens. Password-field auto-detection is not assumed available in pixels.
- Do not capture browser profiles, cookies, authorization headers, storage state, raw network bodies or terminal environment dumps as convenience context.
- Keep recordings private to the owner; do not automatically index them in Library or share them with bots. Authenticate every frame/timeline/export endpoint and prevent another bot's recorder from reading the session.
- Owner edits produce a separate reviewed evidence revision. Model processing uses only the frozen selected revision; new edits revoke its prior processing/test binding. Only necessary selected images/text go to the explicitly configured provider.
- Screen contents, page instructions and demonstration text are untrusted evidence. They cannot approve writes, enable tools, change credentials, install skills or add management grants.

Redaction/scanning can assist review but is not proof of secret absence. Acceptance covers the explicitly supported protected-input paths and synthetic canaries. If confidential content was captured outside those boundaries, support scoped removal and clearly distinguish deletion from the app from data already sent to a provider or retained by a separate application.

## 9. Storage, retention and resource limits

Use PostgreSQL for session/manifest/job metadata and the existing object-store client for private binary evidence. Store retained payloads under an authenticated teaching-specific namespace with application encryption before durable storage, using existing cryptography/key-management conventions. Do not make raw captures ordinary shared artifacts or add a public bucket/host port. Small control/receipt records contain hashes, closed error codes and counts, not screenshots or typed values.

The adapter uses bounded volatile capture buffers. If private persistence cannot keep up, pause recording and show the gap; do not write plaintext fallback captures to bot workspaces or silently drop arbitrary actions. Capture can fail without changing ownership or replaying user actions.

Stage object uploads, verify digest/length, then commit manifest references through short revision-checked transactions. Preserve logical sequence identity across retries. Reconcile unknown upload results and garbage-collect unreferenced staged objects; a database failure must not advertise missing chunks as retained. Never keep a database transaction open during model generation or a large upload.

Proposed starting limits for the feasibility trial: at most two retained frames/second, 2 MiB/frame, 256 MiB/session, one generation job at a time for this local workspace and a visible soft duration warning. Measure legibility, CPU, memory, latency and storage against the existing bot allocation before adopting these values. Hard resource exhaustion pauses capture with recovery instructions; it is not a new agent execution deadline. No continuous-video/transcoding or local heavyweight vision stack is selected.

Proposed retention defaults: unpinned source recordings/unused derivatives expire after seven days, with visible expiry and explicit Keep/Delete actions; discard removes private source payloads immediately through a retryable deletion job. Published skill text and minimal provenance remain independently versioned. A skill must not require an expiring recording URL to run. Deletion/expiry cancels pending generation, invalidates late results and handles referenced test/draft data explicitly. A policy may preserve approved text while deleting source images; state that distinction before confirmation. No claim of physical erasure from backups/provider history is made by deleting a live object.

## 10. Draft generation

Use a bounded application processing job with the existing provider credential/configuration and authenticated outbound patterns. This is a data-to-draft transformation; it receives no execution tools or authority to apply the generated skill. It does not change the normal bot's available tools. A usable vision-capable provider/model must be verified; an OpenAI-compatible text endpoint does not establish image support.

Normalize events first, process bounded reviewed segments, then assemble a structured procedure. Persist exact source/evidence hashes, prompt/schema version, provider/model identity and job generation. Retries resume the same job/input revision; cancellation, deletion or edits fence out late results. Invalid output gets bounded schema repair and a visible failure, not silent installation or an unbounded provider loop. Cap frame/token/cost budgets; retain useful completed draft segments after a processing failure.

Draft fields:

- Goal, suitable-use description and explicit non-applicability conditions.
- Typed/named parameters, sample placeholders and their source; secret inputs refer to a private authentication step, never a value.
- Current app/tool/account prerequisites, expected starting state and surface identity.
- Ordered actions with intent, available target evidence, expected observation and verification method.
- Which steps were observed, owner-added or inferred; link to non-expired permitted evidence where available.
- Explicit branch/loop conditions only when demonstrated or owner-confirmed; otherwise request another example or leave the capability narrower.
- Recovery behavior for missing targets, changed layouts, expired login, partial completion and uncertain effects.
- Required collaborators and authority boundaries where delegation is part of the procedure.

Emit a lean self-contained `SKILL.md`, valid frontmatter and a descriptive owner-managed name of at most 64 characters. Avoid credentials, historical artifact IDs, private paths, raw incident transcripts, fixed screen coordinates as universal targets, and assumptions that a saved account/session remains authorized. Keep skill consultation non-preprocessing as in the current plugin. Do not automatically generate executable scripts or embed hidden actions in templates.

## 11. Testing an unpublished candidate

Test an immutable draft revision, not whatever is currently open in the editor. Bind test admission to target bot/configuration, source evidence hash, exact skill bytes/hash, parameters, declared output checks and selected current authority. An edit or material environment change makes a previous pass stale; show what changed rather than deleting its history.

Proposed minimal isolation: extend the existing Hermbot capability/middleware path with a task-scoped candidate skill view. Only a teaching-specific authenticated Test action can create this descriptor. It authorizes consulting one exact candidate in that test task; it does not install it globally, change the bot's enabled-skill list, or add tool/connector/management permissions. Integrate `service.create_task` reference validation, preparation fingerprints, skills discovery and `skill_view` together; ordinary disabled-skill checks remain unchanged.

The existing supported tool-execution middleware already wraps native `skill_view`. Return the candidate through that same result contract for the admitted test invocation, using the actual authenticated session/task and candidate digest. Verify the contract against the pinned native implementation in the feasibility trial. Do not patch Hermes, intercept unrelated invocations or replace an active production skill while testing. If the supported hook cannot express this faithfully, stop that implementation path and resolve test isolation before shipping; do not fall back to temporarily overwriting live skills or claiming a pasted prompt tested an installed skill.

Use normal tasks, tool receipts, work results, budgets, Stop and owner decisions. Start on a designated local fixture app or explicitly chosen test resources. A live test is a real run: there is no universal dry-run switch for arbitrary GUI/native actions. A timeout on an uncertain write must lead to observation/reconciliation, not replay from step one. Missing access or dependencies remain blocked rather than prompting automatic privilege changes.

At least one fresh-input run is needed for a basic procedure; data-dependent loops/branches need representative additional cases. Test assertions use actual resulting files/records and correct target identity where available. A task ending or an attractive screenshot is insufficient. Distinguish model completion, assertion pass, failure, owner cancellation and uncertain effects. Preserve the test report and exact candidate identity.

Native tests retain current continuous-execution behavior. Segmentation applies to capture/generation processing and introduces no new checkpoint/yield protocol for Hermes tasks.

Candidate consultation and installed-native consultation are separate evidence. After reviewed publication, verify native readback/hash and perform a controlled native skill-load/use demonstration as part of milestone acceptance. Do not call an overlay-only preview proof of native installation.

## 11a. Feedback-driven teaching revisions (November 2, 2026)

The owner should demonstrate, answer necessary questions, inspect results and approve publication; maintaining Markdown is an optional advanced path. The first implementation omitted this loop and therefore did not meet the intended teaching experience.

- New candidate tests opt into automatic draft improvement. Settled tests, their authenticated owner answers (including chat-routed answers), explicitly associated owner messages/steering, recorded outcome states and owner verification observations feed a bounded application processing job. Historical tests require an explicit Improve action; do not silently reprocess old conversations during migration.
- Freeze the parent draft/evidence, sources, provider revision and job generation. Source-linked changes distinguish owner instructions, execution-specific directions, recorded outcome states and model reports; a completion report is not independent proof. Preserve complete owner instructions within the bounded input limit; report oversized input rather than silently dropping the end of an instruction. Truncated model reports are explicitly labeled.
- Automatically prepare an immutable successor draft and a readable explanation. Reuse previous confirmed feedback across revisions and failed-job retries. Do not turn one-off Stop/leave-as-is directions into permanent workflow behaviour, infer permissions from page contents, or grant broader authority.
- Missing knowledge becomes at most five focused questions, not generic SAFE_PREREQUISITE/TARGET_MAP placeholders. Questions belong to the teaching session and exact current draft/evidence, not a completed execution. The owner can mark an answer reusable or specific to that test. Once the questions are answered, queue the revision automatically. Changed drafts/evidence invalidate old questions.
- Keep test tasks immutable while feedback accumulates. Wait for terminal tasks and settled native attempts before revision. Fence late results on edits, cancellation, expiry, source deletion or changed provider configuration. Deduplicate identical feedback and do not retry failed provider calls endlessly.
- Suggest editable fresh test inputs and meaningful checks, including negative cases. Preparing a draft does not start a test, publish it, enable a native skill or approve external effects. Test reports distinguish execution end, unverified checks, owner-recorded failure, verified pass and historical versions. Publication cannot reuse an earlier version's pass.
- Keep existing draft history and native CAS/readback/refresh. No Hermes source change, global candidate installation, generic agent framework or additional queue service is needed.

Acceptance must include: answer during a test; automatically revised skill uses that answer in a later test; one-off instructions remain scoped; unrelated chat/other bots/manager answers are excluded; stale responses cannot overwrite owner edits; expired/deleted learning data is cleared; failed/untested work never gets an automatic passing badge. The existing Zenpad recording and tests are the first real continuation case, not a request to record again.

## 12. Publication, enablement and rollback

Publication checks current owner authorization, target bot, draft revision/hash, selected input/evidence revision and test binding. New generation output cannot reuse an earlier publication decision. Untested drafts may remain saved drafts; the teaching flow labels a version tested only when its current binding passes. Any manual skill editing outside this flow retains the existing owner's controls without inventing a tested badge.

Hand back the computer, settle native execution and reuse `knowledge.guard`, native compare-and-swap, `knowledge_versions` and queued bot configuration. Add a teaching provenance link to the saved version, not a fabricated completed-task source ID. The operation progresses through queued, native write/readback, runtime refresh and ready. A lost response is reconciled using its existing operation ID and content hash before retrying. A successful write with a pending refresh remains pending; it is not ready.

Explicit Enable applies only to that bot's skill selection. Native packages/tool availability and connector permissions remain independently configured. A manager teaching/deploying to another bot still requires current configure authority and the exact owner decision where applicable. Copying to another bot requires explicit target selection, prerequisite checks and appropriate test evidence.

Rollback loads historical content as a new draft/version against the current native hash, as the current UI does. It does not mutate historical versions, undo completed external effects or silently preserve a stale tested badge. Disable/remove preserve provenance and make dependent routines/work requirements visible through existing capability checks.

## 13. Minimal records and API contracts

Prefer four small record families plus existing work/knowledge/operation records:

| Record | Main contents |
|---|---|
| Teaching session | Owner, bot, viewer binding, desktop/runtime epoch, recording generation/state/revision, capture policy/limits, activity/expiry and closure reason |
| Evidence manifest/chunks | Session/segment/sequence, content hashes and encrypted object references, geometry/time bounds, privacy/gap markers and selected evidence revision |
| Draft revisions/jobs | Immutable structured content and Markdown hash, source selection hash, target, processing state/generation, model/schema versions and published knowledge-version link |
| Teaching tests | Candidate hash/config/input/check hashes, ordinary task/work IDs, actual result evidence and status |

Proposed owner routes under `/api/v1/bots/{bot_id}/teaching`: create/list/detail; expected-revision start/pause/resume/finish/discard; authenticated timeline/frame access; evidence selection; generate/cancel; draft revision; test; publish; delete source. Use existing skill enable/version routes where they already fit. This is a proposed route family, not an endpoint count requirement.

Private adapter start/pause/resume/stop commands and chunk delivery carry the exact recording generation and desktop epoch. Browser requests never contain an arbitrary filesystem path, object-store key, provider key or another bot's recorder credential. Server code selects identities and verifies ownership at every boundary. Export, if offered, is an explicit authenticated sanitized derivative; it is not part of the ordinary artifact auto-share path.

Use current SSE/event invalidation and explicit canonical detail APIs. UI refresh/reconnect must not create a new recording/test/job. Do not put screenshots, typed text or model payloads in general SSE/diagnostic events.

## 14. UI states and accessibility

Place Teach in the existing Computer surface and link recordings/drafts from Skills. Reuse existing panels, typography and buttons; do not introduce another app-level navigation area. Review the eventual implementation using the frontend taste skill and real screenshots.

Expose preparing, recording, ordinary/privacy pause, interrupted, saving, ready-to-review, generating, editing, testing, blocked, failed, publishing and ready states. Each error identifies the retained data and available next action. Recording indicators must remain visible during interaction; status also uses text, not color alone. Keyboard users can start/pause/finish, inspect steps, redact/remove evidence, edit a draft and inspect the test result. Preserve focus and edited drafts across recoverable errors.

Closing the pane pauses recording but retains human computer control. Reload, logout, owner-session expiry, bot restart and viewer reclaim all reconcile current authoritative state. A returning page shows the existing session and its gap/paused state; it never resumes capture automatically. Stopping generation is separate from stopping an already-running test task. Publish, Enable and Hand back remain explicit operations with understandable scope.

## 15. Recovery matrix

| Failure | Required result |
|---|---|
| Viewer disconnect/session expiry | Pause capture, reject further authenticated append, retain prior committed prefix and human ownership |
| New viewer/lease epoch | Old producer fenced; explicit session takeover/resume creates a new segment |
| Adapter/runtime restart | Interrupted recording, no automatic input replay; recover committed evidence, verify current desktop before resuming |
| API/worker restart | Durable state/jobs retained; recover the same operation IDs, not new sessions/tests |
| Unsupported input/geometry | Visible incomplete/segmented evidence; no guessed keystrokes or target locations |
| Privacy transition races screenshot | Drop old-generation result and pending text; secret bytes never enter durable capture |
| Object storage full/unavailable | Pause capture, report retained prefix; no plaintext or unbounded memory fallback |
| Unknown upload acknowledgement | Reconcile staged object/hash/manifest; no duplicate sequence or advertised missing data |
| Provider failure/invalid output | Retain reviewed source and completed draft work; bounded retry/repair, visible failure |
| Edit/delete during generation/test | Fence late outputs; historical reports stay attributed to their original versions |
| Candidate test blocked/failed/uncertain | No tested badge or automatic publication; owner can inspect, edit or use normal recovery |
| Native skill changes before publish | Conflict and new review against current hash; preserve the newer skill |
| Write succeeds, refresh fails | Pending/failed configuration state and reconciliation; no false ready state |
| Stop during test/external operation | Reuse existing stop/receipt handling; describe uncertain effects without claiming rollback |
| Evidence retention expires | Delete private source payloads and state provenance availability; self-contained published text remains usable |

## 16. Implementation slices

1. **6.2.0 Feasibility and contracts:** prove accepted RFB observation with current noVNC/TigerVNC extensions, coordinate/keyframe alignment, privacy-generation barriers and the supported task-scoped candidate contract. Verify a configured vision model, capture resource measurements and object encryption/persistence approach. Settle supported input formats and final limits. These are implementation gates, not claims that the plan has already validated them.
2. **6.2.1 Capture lifecycle:** additive metadata migration, authenticated adapter/session protocol, chunk persistence, state transitions, single-session fencing, UI controls and reconnect/partial-prefix handling. No model processing yet.
3. **6.2.2 Private input and evidence review:** acknowledged secure-input pause, late-frame rejection, timeline/keyframe review, parameters/redactions, retention/discard and immutable selected evidence. Canary privacy tests must pass before real non-fixture capture.
4. **6.2.3 Procedure generation:** bounded processing jobs, schema validation, cancellation/staleness, editable observed/inferred steps and compatible Markdown generation. Test with synthetic and owner-reviewed fixture captures first.
5. **6.2.4 Exact candidate testing:** integrate task admission/capabilities/middleware, ordinary work/decision/result records, selected authority, fresh inputs and meaningful assertions. No global test enablement or skill overwrite.
6. **6.2.5 Publication and history:** connect reviewed draft/test identity to native writes, readback, runtime refresh, enable/disable, rollback and provenance. Keep current knowledge workflows functional.
7. **6.2.6 Real acceptance and final review:** real owner demonstrations, multiple workflows, failure/restart/concurrency checks, UI/accessibility review, relevant earlier regressions, unchanged-Hermes/private-state audit and guarded fixture cleanup.

Do not wait for 6.3's catalog to supply skill review/testing or for 6.4 to supply necessary recording controls. Phase 6.1 voice remains held; teaching uses typed annotations. Mobile-device capture follows separate Phase 6A planning.

## 17. Acceptance matrix

| Area | Required evidence |
|---|---|
| Real capture | Actual owner records a selected bot desktop; accepted click, typing, scroll, drag and shortcut coverage; correct native geometry at multiple viewer sizes |
| Ownership | View-only injection rejected by existing gate; stale viewer/epoch cannot append; admission drain honored; two-tab reclaim and disconnect preserve explicit handback |
| Protocol | Fragmented/coalesced input, QEMU keys, clipboard policy, IME/unsupported input and resize tested without false semantic claims |
| Privacy | Synthetic secret canaries absent from persisted actions/images, object metadata, app logs, SSE, model request and generated/published skill across pause, paste, Enter/Tab helpers and late frames |
| Review | Owner edits/removes steps/images and parameterizes values; source-selection revision/hash changes invalidate downstream claims |
| Generalization | Fresh inputs and changed example records succeed; at least one negative/missing-prerequisite test and an additional branch/loop case where supported |
| Generation | Actual configured provider accepts selected image/text input; cancellation, invalid output, outage, token budget and late results handled |
| Candidate isolation | Exact candidate consultation proven; normal tasks retain ordinary enabled skills; no native/global skill changes before publish |
| Outcome evidence | Actual resulting local files/records and identity checks; model prose or click acknowledgement alone cannot pass a test |
| Collaboration | Taught manager procedure delegates to current permitted teammates, reads evidence and accepts required outputs; revoked/missing grants remain rejected |
| Publication | Exact reviewed bytes/version only, concurrent native-edit conflict, same-content replay, write/readback/refresh distinction, current authority |
| Native use | Installed native skill bytes match publication; normal Hermes consultation/use succeeds after refresh without a preview overlay |
| Recovery | Interrupted capture, API/worker/adapter restart, duplicate boundary commands, storage failure, stale generation and uncertain test effect preserve truthful state |
| Lifecycle | Version history/rollback, disable/remove, source deletion/expiry and cleanup retain historical evidence and preserve unrelated owner state |
| UI | Keyboard/focus/screen-reader labels, long timelines/content, dark/light, 1440/768/390/320px, reload/reconnect and no horizontal overflow |
| Regression/integrity | Current knowledge/computer/library/work/review/routine/notification tests; unchanged upstream Hermes source; original private bot volumes/groups/routines/connections preserved |

## 18. Real demonstration set and exit

Use an isolated local fixture app/data directory and existing bots. Obtain the actual owner's recording/review/publish choices in Hermbot; do not simulate them with API calls and call them owner acceptance. App setup and synthetic fault injection remain separately labeled.

1. Demonstrate a browser form-to-record workflow, including a protected authentication-like canary step, a changed input value and an independently read saved record.
2. Demonstrate a desktop/file workflow with keyboard/drag or clipboard omission handling and a concrete resulting file checked on new inputs.
3. Create a collaborative procedure from the captured workflow plus explicit owner-authored delegation steps; label those steps as added rather than observed. Execute with a current scoped manager grant, inspect actual contributions and verify missing/revoked authority separately.

Show at least one owner correction/additional example, one interruption/reconnect and one publication conflict/rollback. The first two demonstrations must use actual owner actions; published native use must load the actual installed bytes. If supported app/permission/resource constraints prevent a required case, retain an explicit blocked milestone rather than substituting a mocked success.

Before closing 6.2: review/fix the change, run proportionate current regressions, verify real teaching outcomes and privacy boundaries, audit unchanged harness/private-state integrity, archive only fixture work/groups and remove temporary candidates/captures/grants. Preserve owner-approved skills and any source recording the owner explicitly chose to keep. Record exact outcomes and the supported/unsupported capture surfaces.

## 19. Decisions and remaining validation

Proposed choices are action/keyframe recording, self-contained Markdown skills, adapter-side accepted-input observation, explicit protected-input barriers, reviewed model evidence, a task-scoped candidate preview, ordinary native publication and PostgreSQL-backed jobs. They are ready to guide implementation; they are not guarantees of universal GUI learning or secret detection.

The feasibility slice must settle: the observer library/implementation, exact native middleware result contract and capability fingerprint changes, supported keyboard/IME/clipboard cases, image readability/resource limits, verified vision-provider support, encryption implementation and owner-visible retention defaults. Prototype these against the pinned runtime before depending on them. UI/browser automation cannot substitute for actual owner desktop teaching/privacy confirmation. Broad system evaluations, new connector platforms, universal exactly-once GUI writes, mobile-device teaching and VM migration remain outside this milestone.
