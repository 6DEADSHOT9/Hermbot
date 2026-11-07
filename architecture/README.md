# Hermbot architecture explorer

This folder holds an interactive map of the whole Hermbot project: every file, service, route, table, tool, feature and flow, with the reasoning behind each feature. Everything here is generated from the repository and checked against it.

## Open it

Open `index.html` in a browser. It needs an internet connection only to load the graph library.

Start with the **Start here** page. It explains what Hermbot is in plain words, shows a simple picture of the parts, and suggests an order for the other views.

## What is in this folder

| File | What it is |
|---|---|
| `index.html` | The explorer. One self-contained page with the full model inside. |
| `model.json` | The data behind the page: nodes, links, features, flows, state machines, discrepancies, plain-language text. |
| `coverage-report.md` | The result of the completeness check, in readable form. |
| `build_model.py` | Reads the repository and extracts facts mechanically (imports, routes, tables, compose services, UI calls, tools, file hashes). |
| `merge_model.py` | Combines those facts with the notes written by reading every file, then renders `index.html`. |
| `check_coverage.py` | The completeness check. It fails if any file, route, table, column, service, setting, tool or UI call is missing, if any claim lacks a code reference, if any secret value leaks, or if a file changed since it was last read. |
| `refresh.py` | Keeps the explorer in step with the repository (see below). |
| `explorer_template.html` | The page design; `merge_model.py` fills it with the model. |
| `_work/` | Working files: extraction output, reading notes, screenshots and the read ledger. |

## When the repository changes

Files will keep being added and edited. To bring the explorer up to date:

1. Run `python3 architecture/refresh.py`. It re-scans the repository and lists every file that is **new**, **changed** or **deleted** since it was last read, with a suggestion of which reading pass should handle it. The same list is saved in `_work/refresh-queue.json`.
   - It also moves existing `path:line` citations so they still point at the same code. Every file that has been read is snapshotted (`_work/snapshots/`), and `_work/cite_base.json` records which version each file's citations refer to.
   - When a file changes, a line-by-line diff rewrites the citations in `_work/agents/*.json`.
   - Citations that land inside an edited region go into `_work/remap/needs_check.json` to be checked by hand.
2. Read those files and update their notes (in `_work/agents/`). The brief for this step is `_work/REFRESH_BRIEF.md`; the unified diffs of edited files are in `_work/remap/diffs/`.
   - A refresh note named `*_refresh` takes priority over the earlier notes for the same file.
   - It can also carry `citation_checks` (corrected lines for the items in `needs_check.json`) and `corrections` (exact text in earlier notes that the edit made false).
3. Apply those fixes with `python3 architecture/refresh.py --apply NAME`. Every change is logged in `_work/remap/applied.json`.
4. Mark the files as read: `python3 architecture/refresh.py --mark-read path/one path/two --by your-name`.
5. Run `python3 architecture/refresh.py` again. It rebuilds the model, re-renders `index.html` and runs the completeness check. The check only passes when every file has been read at its current content.

The coverage badge in the page header and the **Freshness** section of the coverage report show when the model was built and whether anything is still waiting to be read.

## Rules this folder follows

- Nothing outside `architecture/` is written by these tools; the rest of the repository is only read.
- `.env` values are never printed or stored. The check compares them by hash and redacts any that would appear.
- The code is treated as the source of truth. Where documents disagree with the code, the explorer records a discrepancy.
