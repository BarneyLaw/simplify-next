# AdaptSG Agent Rules

These instructions apply to every coding agent working in this repository. They protect the product's central promise: an itinerary is useful only if it remains safe and honest.

## Product invariant

The LLM proposes; deterministic code validates.

An agent must never let model output directly become an accepted itinerary. Every plan and replan must pass `ItineraryValidator` after all routes, costs, locations, opening hours and accessibility claims have been supplied by typed tools or the curated catalog.

## Safety rules

1. Keep hard constraints in `HardConstraints`, never only in prompt or chat text.
2. Never silently relax wheelchair access, maximum walking distance, lunch time, finish time, rest interval, required venues or total budget.
3. Treat a venue as wheelchair-accessible only when `accessibility_status == verified` and a source is present. Exclude unverified venues when access is mandatory.
4. Numerical route, distance and cost values must come from a routing or cost tool. The LLM must not invent them.
5. If no feasible plan exists, raise `NoFeasibleItinerary` and ask for a user decision. Do not fabricate a workaround.
6. Treat fatigue as a request for less travel, shorter activity and more rest. Do not diagnose or offer medical advice.
7. Do not add booking, payment or purchase tools without an explicit product/security review.
8. Require caregiver approval when a proposal exceeds the configured cost-increase threshold.
9. Keep replanning bounded by `ADAPTSG_MAX_REPLANS`, which may not exceed three.
10. On a live-tool failure, retain the current plan and report that verification failed.
11. Preserve and display source timestamps. Never represent demo estimates as live data.

## Architecture boundaries

- `domain.py`: immutable Pydantic state and enums. Add state here before relying on new conversation text.
- `preference_parser.py`: Bedrock extraction and conservative fallback only. No route or budget decisions.
- `tools/`: external facts and numerical values. Keep return payloads small and typed.
- `planning.py`: deterministic candidate selection, scheduling, minimal-change scoring and replanning.
- `validation.py`: final authority for hard-constraint compliance.
- `agent.py`: bounded orchestration and approval boundary.
- `streamlit_app.py`, `src/adaptsg/ui.py`, `web_api.py`: presentation and transport boundaries; no safety logic.

Do not duplicate a core rule in a UI or prompt. Put it in the domain or validator and test it.

## Required Git workflow

1. Start every feature on a branch named `feature/<short-kebab-name>`.
2. Do not commit feature work directly to `main`.
3. Make a Conventional Commit after each coherent logic change, for example `feat:`, `fix:`, `test:`, `docs:`, `build:` or `ci:`.
4. Keep generated files, secrets and unrelated changes out of commits.
5. Run the proportional checks before each commit. Run the full correctness suite before merge.
6. Merge completed branches with a non-fast-forward merge so the feature boundary remains visible.
7. Never force-push, reset, rewrite shared history or discard another contributor's work without explicit approval.
8. Update `PROGRESS.md` when a feature is merged, a blocker is discovered or a metric changes.

## Team role activation

When a user starts a task with `ROLE 1`, `ROLE 2`, `ROLE 3`, `ROLE 4`, or the matching role
title, read `TEAM_WORKFLOW.md` and the corresponding file in `docs/roles/` before editing.
Treat that role's path list as an ownership boundary. If the requested change crosses an
ownership boundary, prepare the documented handoff or contract-change request instead of
editing another role's files without coordination.

If the user states only a role and no task, select the highest-priority unfinished item in
`PROGRESS.md` that is inside that role's ownership, state the selection, and proceed. Every
role remains subject to all product, safety, Git, coding, correctness, and definition-of-done
rules in this file.

## Coding standard

- Target Python 3.12 and use full type annotations.
- Prefer frozen Pydantic models at trust boundaries and pure functions for scoring/validation.
- Reject undeclared input fields. Do not pass unvalidated dictionaries between layers.
- Bound every loop and every external request. Live HTTP calls currently use eight-second timeouts.
- Catch provider errors only at boundaries and translate them to a domain error with an actionable message.
- Keep provider credentials in environment variables or managed secret stores. Never commit `.env` or AWS credentials.
- Keep demo adapters deterministic so CI and the recorded demo remain reproducible.
- Add a regression test for every bug and every new hard constraint.

## Correctness gates

Activate the virtual environment, then run:

```powershell
./scripts/check.ps1
```

or:

```sh
./scripts/check.sh
```

The gate includes formatting, lint, strict typing, at least 90% branch coverage, 20 named evaluation scenarios, Bandit, dependency audit, Vercel JSON and browser-script syntax. Docker and AWS SAM builds run in GitHub Actions.

## Definition of done

A feature is done only when:

- its state and permissions are explicit;
- hard constraints still cannot be bypassed;
- expected and failure paths are tested;
- demo and live provenance remain distinguishable;
- relevant documentation and `PROGRESS.md` are updated;
- local correctness gates pass;
- no secret, generated artifact or unsupported claim is committed.

