# AdaptSG four-role workflow

This file turns the four-person split in the project brief into collision-free ownership of
the repository as it exists today. The brief's proposed directory tree was conceptual; do not
create duplicate `agent/`, `planning/`, or `validation/` packages.

## Start a coding-agent session

Use one of these declarations as the first line:

```text
ROLE 1
TASK: <optional task>
```

`ROLE 2`, `ROLE 3`, and `ROLE 4` work the same way. The full role title is also accepted.
The coding agent must:

1. Read `AGENTS.md`, `PROGRESS.md`, this file, and its role file under `docs/roles/`.
2. Echo the selected role, owned paths, feature branch, and work item before editing.
3. If `TASK` is omitted, choose the highest-priority unfinished in-scope item from
   `PROGRESS.md` and state the choice.
4. Create `feature/r<role-number>-<short-kebab-name>` from the current integration point.
5. Modify only owned paths. Preserve unrelated dirty work and request a handoff for other
   paths.
6. Keep the deterministic demo path runnable and add regression coverage in the allocated
   test files.
7. Run proportional checks while coding and the complete `scripts/check.ps1` gate before
   merge.
8. Commit coherent changes using Conventional Commits and merge completed work non-fast-
   forward after review.

## Ownership map

| Role | Title | Role file |
| --- | --- | --- |
| 1 | Agent and integration lead | `docs/roles/role-1-agent-integration.md` |
| 2 | Tools, data, and deterministic planning | `docs/roles/role-2-tools-planning.md` |
| 3 | Frontend and demo experience | `docs/roles/role-3-frontend-demo.md` |
| 4 | Guardrails, evaluation, deployment, and presentation | `docs/roles/role-4-quality-platform.md` |

Shared contracts in `src/adaptsg/domain.py`, configuration in `src/adaptsg/settings.py`,
dependency metadata, and `.env.example` have Role 1 as their sole editor. Schemas are frozen
except for blocking fixes. A required change starts with:

```text
CONTRACT CHANGE | symbol/file | reason | affected roles | migration | tests
```

Role 1 and every affected owner review it; only the owning role edits the shared file. The
dependency direction remains UI -> service/agent -> tools/planning -> validator. No role may
duplicate a safety rule in a prompt or presentation layer.

## Daily cadence

| Time | Team action |
| --- | --- |
| 09:00 | Sync from the integration point; state role and task. |
| 09:15 | Check contracts and run the deterministic mock smoke path. |
| Morning/afternoon | Work only in owned paths using small coherent commits. |
| 15:30 | Run proportional tests and prepare the handoff. |
| 16:00 | Handoff cutoff for the day's integration slice. |
| 16:15 | Role 1 performs interface and integration review. |
| 17:00 | Non-fast-forward merge of a runnable slice; Role 4 runs the full gate and updates `PROGRESS.md`. |

The daily exit invariant is a runnable mock path, zero validator-accepted hard-constraint
violations, visible demo/live provenance, and no unreviewed shared-contract change.

## Handoff format

End a role session with one line that another agent can act on:

```text
HANDOFF | role | branch/commit | changed paths | contract/API effects | tests/results | demo/live provenance | blocker/next
```

Role 4 is the status steward and is the only routine editor of `PROGRESS.md`. Other roles send
verified facts through the handoff. A blocker, merged feature, or changed metric must still be
recorded as required by `AGENTS.md`.
