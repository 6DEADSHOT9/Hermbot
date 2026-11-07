# Coverage report

**Result: PASS** — 19/19 checks pass. 786/786 files represented; 2043 nodes, 8520 edges; 10397 files in excluded directories are listed, not modelled.

| Check | Result | Detail |
|---|---|---|
| Every non-excluded file maps to a node (count matches a fresh walk of the repo) | pass | 786/786 files represented; inventory=786 |
| Every Python module has a node | pass | 87 modules |
| Every HTTP route has a node | pass | 166 routes |
| Every table and column is present | pass | 72 tables, 786 columns |
| Every compose service, network, volume and port is present | pass | 26 compose objects |
| Every env var name is present | pass | 50 env var names |
| Every plugin tool and hook is present | pass | 26 tools/hooks |
| Every web file is present | pass | 36 web files |
| Every UI API call is recorded and either mapped to a route or explained as a discrepancy | pass | 125 calls; 125 mapped to routes; 0 explained as discrepancies |
| Every binary, evidence and archive file was opened and summarised (image size, JUnit counts, JSON keys, gzip records) | pass | 488 files opened mechanically |
| Every feature has every panel section filled | pass | 21 features x 11 sections |
| Every design rationale is labelled documented/inferred/unknown | pass | 337 decisions |
| Every flow (a–l and any added later) has steps and every step is evidenced | pass | 181 steps across 13 flows |
| Every node is evidenced or marked unknown | pass | 2043 nodes |
| Every edge is evidenced or marked unknown | pass | 8520 edges |
| Every evidence citation points at an existing file and line | pass | 23985 citations checked |
| No edge points at an unresolved node | pass | 0 unresolved references |
| No .env value appears in model.json or index.html (sha256 token match + substring, values never printed) | pass | 19 .env values checked; 1 non-secret values equal public defaults (PUBLIC_URL) |
| Every file's current content has been read (sha256 matches the read ledger; new or edited files must be re-read) | pass | 785 files hashed; 785 in read ledger |

## Files by category

- backend-asset: 1
- backend-module: 56
- browser-test: 41
- build-output: 3
- compose: 2
- config: 5
- design-snapshot: 110
- doc: 32
- dockerfile: 3
- evidence: 370
- migration: 17
- plan-archive: 7
- plugin: 3
- root-module: 3
- runtime-module: 8
- script: 19
- secret-config: 1
- test: 69
- ui: 36

## Excluded directories (listed, not modelled)

- `.pytest_cache/`: 5 files
- `.ruff_cache/`: 8 files
- `__pycache__/`: 1 files
- `architecture/`: 800 files
- `backend/__pycache__/`: 56 files
- `docker/__pycache__/`: 5 files
- `plugins/hermbot/__pycache__/`: 1 files
- `scripts/__pycache__/`: 3 files
- `test-results/__pycache__/`: 1 files
- `tests/__pycache__/`: 66 files
- `tests/browser/node_modules/`: 513 files
- `web/node_modules/`: 8938 files
