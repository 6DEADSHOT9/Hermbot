# Evaluation research and deferred follow-up

Recorded: 2026-10-19, from the evaluation research discussed in this thread.

**Status: research complete for an initial shortlist; implementation deferred by the owner until after other tasks.** No framework has been selected or adopted. No evaluation package was installed, no new suite was implemented, and no candidate was run against Hermbot during this research. There is no scheduled start or automatic trigger. The earlier suggestion to make evaluations the immediate next step is superseded by this deferral; resume when the owner returns to this work.

**Owner sequencing decision, 2026-10-19: run evaluations before implementing the current issue-fix backlog.** Evaluations should reveal failures beyond those already identified. Preserve a baseline, combine evaluation findings with the issue register, prioritize fixes from that combined evidence, and rerun evaluations after changes. Deferring evaluations does not mean advancing those fixes ahead of them. The intervening tasks have not been specified here.

The comparisons below are a documented assessment of fit based on official documentation and repositories reviewed during the earlier discussion. They are not measured results or a fresh verification of every dependency as of this recording date. Recheck compatibility, release versions and relevant licenses before adoption.

## What we need to evaluate

Hermbot's failures extend beyond final response quality. A convincing answer can conceal an unsent message, an obsolete requirement, an inaccessible file or a stalled collaborator. Evaluation should distinguish:

| Dimension | Evidence and questions |
|---|---|
| Application correctness | Did the current requirement change correctly? Was the actual requested file delivered? Did the intended external action occur exactly as requested? |
| Agent quality | Was the owner's intent understood? Was research grounded? Were tools and collaborators chosen appropriately? Were limitations communicated accurately? |
| Operational reliability | Did work progress, resume and recover correctly? What were latency, repeated-run reliability and cost? |

The system under evaluation should include Hermbot's message intake, backend, worker, coordination, artifacts and connector handling around the real Hermes runtime. A standalone model or Hermes benchmark cannot establish that the whole application works. Keep Hermes source unchanged: any evaluation integration belongs above the harness, using supported interfaces, configuration and Hermbot's own test infrastructure.

## Framework comparison

The recommendations in this table are research conclusions, not approved implementation decisions.

| Candidate | Relevant capabilities documented by its maintainers | Fit and limitations for Hermbot | Research recommendation |
|---|---|---|---|
| **Inspect AI** | Custom Python solvers and scorers, multi-turn/agent execution, Docker sandbox support, evaluation logs and a local viewer. | Can accommodate complete application workflows and state-based scoring. A custom integration must drive Hermbot and collect its evidence; this integration has not been tested. Sandbox support does not automatically isolate every external call made by custom evaluation code. | **Strongest primary candidate** for system evaluation. |
| **Harbor** | Sandboxed agent tasks, local Docker execution, reusable benchmarks and an existing Hermes agent adapter. | Useful for measuring underlying agent capability. Its standard Hermes adapter runs a standalone agent rather than our deployed application with its routing, persistent work and coordination services. Full Hermbot evaluation would require a different integration. | **Optional separate Hermes baseline**, not sufficient application coverage. |
| **DeepEval** | Python evaluation, component/conversation/trajectory scopes, tool-use and grounding metrics, model-judged task completion. | Useful for semantic quality metrics. Its task-completion metric requires trace integration and is judgment-based; it cannot independently prove a file exists or an external mutation occurred. Native Hermes internals are not automatically instrumented merely by adding this library to the backend. | **Optional metrics library** if the primary framework's scorers leave a specific gap. |
| **Promptfoo** | Custom Python and HTTP providers, assertions and comparative evaluations. | Good for intake prompt/model comparisons and structured interpretations. Driving long-lived stateful Hermbot scenarios and gathering backend evidence still requires custom code. It can call an entire application, but does not supply our workflow semantics. | **Strong component-evaluation alternative.** |
| **Braintrust** | Dataset/task/scorer experiments, custom scoring, experiment comparison and evaluation UI. | Useful for shared reporting and repeated experimentation. Introduces a platform/data-handling decision and still needs Hermbot-specific scenarios and success conditions. | **Consider later** if collaborative reporting becomes a priority. |

Primary references:

- Inspect: [solvers](https://inspect.aisi.org.uk/solvers.html), [scoring](https://inspect.aisi.org.uk/scoring.html), [sandbox behavior](https://inspect.aisi.org.uk/sandboxing.html), [logs](https://inspect.aisi.org.uk/eval-logs.html), [viewer](https://inspect.aisi.org.uk/log-viewer.html).
- Harbor: [official repository](https://github.com/harbor-framework/harbor), [Hermes adapter source](https://github.com/harbor-framework/harbor/blob/main/src/harbor/agents/installed/hermes.py).
- DeepEval: [evaluation scopes](https://deepeval.com/docs/evaluation-end-to-end-llm-evals), [task completion](https://deepeval.com/docs/metrics-task-completion), [tool correctness](https://deepeval.com/docs/metrics-tool-correctness).
- Promptfoo: [Python provider](https://www.promptfoo.dev/docs/providers/python/), [HTTP provider](https://www.promptfoo.dev/docs/providers/http/).
- Braintrust: [running evaluations](https://www.braintrust.dev/docs/evaluate/run-evaluations).

## Benchmarks and simulated service environments

An evaluation framework runs and scores experiments. A benchmark supplies tasks. A simulated environment supplies services/state to act on. These are different roles; selecting one does not automatically supply the others.

| Candidate | What the research found | Assessment |
|---|---|---|
| **Agent-Diff** | Locally runnable stateful replicas of Slack, Google Calendar, Box and Linear, deterministic state diffs, and a published 224-task benchmark. The repository identifies an MIT license. | Particularly relevant for proving connector effects and unexpected state changes. Published service coverage does not include our Gmail, Drive or GitHub workflows. Its execution proxies/examples are not automatically wired into Hermbot connectors. |
| **env0** | Local Gmail, Calendar, Drive, Docs and Slack services, REST APIs/web UIs, state snapshots, resets, diffs and action logs. The repository identifies AGPL-3.0 licensing. | Closer service coverage. It is an environment runtime, not a complete evaluation framework. Needs API/auth/behavior compatibility assessment and review of the intended use before adoption. |
| **ClawsBench** | Productivity-agent tasks and separate capability/safety measures. At research time, its public repository still described tasks as forthcoming and published non-commercial terms. | Useful research reference; not established as a ready-to-run foundation for our application. Verify actual public task availability and terms rather than relying on paper-level coverage claims. |
| **ClawMark** | Staged, multi-day workplace tasks, changing external state, multimodal inputs and rule-based checkers. Its repository publishes a non-commercial license. | Useful reference for longer workflows and artifact verification. Its runtime/backends differ from Hermbot; no drop-in compatibility was established. |

Primary references: [Agent-Diff repository](https://github.com/agent-diff-bench/agent-diff), [Agent-Diff paper](https://arxiv.org/abs/2602.11224), [env0](https://github.com/benchflow-ai/env0), [ClawsBench](https://github.com/benchflow-ai/ClawsBench), [ClawsBench paper](https://arxiv.org/abs/2604.05172), [ClawMark](https://github.com/evolvent-ai/ClawMark).

The license descriptions are recorded facts from the research, not a determination of permission for a particular deployment. No benchmark data or environment code was imported into this project.

## Recommended direction from the research

Start the future design discussion with **Inspect AI plus Hermbot-specific scenarios derived from [ISSUES.md](ISSUES.md)**. Use Harbor separately if we need to distinguish underlying Hermes/model capability from application behavior. Add another metrics library or platform only for a demonstrated gap, rather than adopting all shortlisted tools.

For connector scenarios, assess Agent-Diff and env0 as possible environments. Neither replaces application-state checks or requires adoption as part of the initial framework decision.

Likely coverage, to refine when this work resumes:

| Scenario class | Examples from the issue register | What success should establish |
|---|---|---|
| Interpretation and action identity | C15/C16, TEMP-03, M01/M11/M12 | Discussion creates no assignment; a revision targets the right work; a newly requested send is distinct from a completed historical action. |
| Work revisions and lifecycle | TEMP-04/TEMP-07, M06/M10 | Corrections reach waiting work; obsolete requirements retire; cancelled work reopens coherently; derived outputs track relevant changes. |
| Coordination and recovery | C31/C32, M14/M15/M18/M21/M26 | Informational messages do not force blocking work; answers resume once; references and execution semantics survive handoffs; prerequisites hold. |
| Artifact delivery and completion | TEMP-06, M03–M09 | The actual requested downloadable product is delivered, usable and current. A manifest, local path or partial branch is insufficient evidence by itself. |
| Connector outcomes | C22/C23, TEMP-01/TEMP-02, M01/M17 | Correct recipient/action, actual delivery evidence, useful distinction between invalid IDs, missing scopes and revoked authentication, and no duplicate effects. |
| Research and communication quality | KAG-04, TEMP-10/TEMP-11, M13/M16/M27 | Claims are supported by inspected sources; limitations and audiences are accurate; application policy is not attributed to the owner. |
| Progress and privacy | C30/C31, M25/M28 | Observed progress is distinguished from heartbeat/status; failures are diagnosable; secret canaries do not leak through the evaluated paths. |

These are proposed evaluation classes. They are not implemented cases, thresholds or a claim that a framework ships these checks.

Use deterministic checks for observable facts: requirement state, artifact bytes/type/access, provider receipts, state mutations and duplicate effects. Use calibrated model/human judgment for qualitative interpretation, research and communication. Do not use the agent's own completion statement as the sole success signal.

The issue-derived dataset should be supplemented with ordinary successful workflows and new variations. Otherwise, improving scores could mean memorizing our old failures rather than improving general behavior. Repeated runs, independent held-out scenarios and separate reliability/quality/latency/cost results should be considered when designing acceptance criteria. Preserve infrastructure failures separately from model/application failures.

## Existing checks to build on

- [Live intake acceptance](../tests/work_intake_live_acceptance.py): eight scenario types repeated three times, producing 24 interpretation checks. It does not run the whole application workflow.
- [Controlled research-quality acceptance](../tests/result_quality_live_acceptance.py): four synthetic evidence fixtures. It evaluates the grader's distinctions, not factual quality across arbitrary real research tasks.
- [Native work-controller acceptance](../tests/work_controller_native_acceptance.py): real execution, shared artifacts and cross-bot corrections.
- [Coordination acceptance](../tests/coordination_native_acceptance.py) and backend/browser regressions: existing evidence for specific orchestration and UI behavior.

Reuse relevant fixtures and assertions where appropriate. These checks do not constitute adoption of any researched suite, and their prior passing results do not establish that current open issues are fixed.

## When we return to this work

The owner will resume evaluation work after the intervening tasks. At that point:

1. Recheck the shortlist against the application's then-current architecture and the frameworks' current interfaces.
2. Agree on evaluation scope, success conditions, isolation, judge calibration, dataset split and run budget.
3. Select the framework and validate a small representative integration before committing to a broad rollout.
4. Run baseline evaluations before implementing the current issue-fix backlog, retaining per-case evidence and failed runs.
5. Combine newly discovered issues with [ISSUES.md](ISSUES.md), then prioritize and implement fixes from that evidence.
6. Rerun evaluations to measure improvements and regressions against the baseline.

No implementation schedule, framework adoption, external write run or automatic production-data export is authorized by this research note. The original roadmap and unfinished Phase 4 acceptance remain separate; the deferral does not close or reorder those milestones by itself.
