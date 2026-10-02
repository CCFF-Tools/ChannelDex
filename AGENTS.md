# ChannelDex project guidance

ChannelDex is owned, developed, and published by Capital Media Systems. PUB-TV
remains the accepted first station boundary and compatibility identifier.

## Current phase

This repository is in planning and contract definition. Treat the product plan, data model, architecture, and decisions documents as the source of truth once they exist. Do not imply that a runnable app, deployment, integration, or production data pipeline exists. Implementation begins only after the user confirms the complete plan and approval gate.

An optional local routing reference may exist at `.codex/SUBAGENT_ROUTING.md` or in the agent environment. It is a convenience for local orchestration; no absolute workstation path is required by this project.

## Documentation truth

Label statements as **proposed**, **accepted**, or **open**. Proposed material is a design suggestion, accepted material is confirmed by the user or recorded decision, and open material requires resolution. Do not turn an assumption into a product invariant or report a planned capability as implemented.

## Product invariants

- Preserve confirmed broadcast history and its source and actor provenance; manual history entry is the initial path and future imports must be additive and auditable.
- Any future import preview must show field mapping, conflicts, and validation before commit, with idempotent operations and stable source identifiers.
- Do not assume vendor APIs, credentials, or service availability; adapters remain replaceable.
- PUB-TV is the first station boundary. GOV-TV is a later boundary and must not expand the initial product scope.
- The product must not require external hosting, a cloud runtime, a CDN runtime dependency, or an AI runtime.
- Protect privacy through data minimization, explicit access boundaries, and no unnecessary external transmission.
- Initial operation is one computer with one owner/editor. Do not overbuild roles or permissions; public access is deferred.
- Restrict the server to loopback by default. Shared LAN access and accounts are later options only if needed.
- V1 covers playlist preparation and media-management notes while retaining scheduling, catalog, and history as core scope. Leightronix file/schedule exchange is a future roadmap item, not an MVP prerequisite.
- Episode reuse follows a premiere-to-premiere broadcast cycle, not the Monday calendar boundary. A replay after a premiere uses that premiere's episode until the next premiere; a replay earlier in the calendar week than the premiere still belongs to the prior cycle. By default, staff choose an older episode case by case. The owner may enable a global standing authorization to carry the most recent explicitly planned episode into otherwise unassigned current/future weeks. Every generated cycle must be visibly labeled and audited; an explicit episode or No program choice wins, a No program cycle stops later carry-forward, disabling the setting stops future generation without deleting existing plans, and this never consumes or advances the pending-new queue.
- V1 plans the full 24-hour broadcast day, including filler, station IDs, PSAs, live events, and programs. The app plans the whole day manually; it does not exchange schedules or control broadcast equipment.
- Reusable non-episode assets and timed live entries are first-class schedule items.
- Keep a complete planned day distinct from evidence that a day actually aired.
- Episode order follows producer direction, usually FIFO. Track intended air order and an optional intended premiere date per unaired episode; producer direction wins. After a premiere passes with a recorded uploaded matching schedule revision, classify the episode as previously scheduled and advance the pending queue idempotently; this is not proof of airing or automatic assignment. Cancelled or known failed premieres require owner review. Future premieres and those lacking a valid matching upload remain pending. Reruns do not consume the pending-new queue.
- Record submission source separately from storage destination. Dropbox, email, and local SMB references are private metadata only; do not require credentials, automatic fetching, connector downloads, network mounts, or V1 integrations.
- Manual V1 media workflow records source availability, Adobe Media Encoder preset/version/machine/output, exact FTP transfer/device, WinLGX library registration and slot assignment, and uploaded schedule revision. Source and derived encoded renditions remain distinct; app notes never imply automated validation or airing.
- Program starts are fixed: a 7:00–7:30 slot always starts at 7:00. The station returns to its virtual channel between programs for schedule, PSA slides, and rotating graphics; expected intervals are intentional and separately labeled from genuinely unknown coverage.
- A 60-second gap between programs is an ideal soft target. Flag episodes that exceed their fixed slot; do not automatically shift, trim, add filler, or control playout.

## Agent delegation

Use the cheapest adequate model and effort; these are project heuristics, not benchmark or capability promises. Honor fixed custom-role model assignments rather than overriding them, and prefer an exact specialized agent when one fits. Use generic explicit Terra only when no specialized role fits.

- Luna: low effort for mechanical or narrow discovery work; medium for bounded implementation when a specialized role fits.
- Terra: medium effort for ordinary multi-file reasoning and product or data contracts.
- Sol: low effort for focused review and medium for cross-cutting work; high only when needed.
- Astra: exceptional high effort only for unresolved architecture or high-consequence reasoning; do not make it the default.

Do not routinely escalate or spawn every tier. Conserve aggregate tokens as well as cost; do not manufacture tiny tasks. Keep forks small (`fork_turns="none"` when context is unnecessary), assign explicit non-overlapping ownership, and request reports of 300 words or fewer. Run the complete affected test set using the shortest practical command; do not skip relevant checks or add redundant reviews. Preserve concurrent edits.

The available project models are gpt-5.6-luna, gpt-5.6-terra, gpt-5.6-sol, and gpt-6-astra. Model positioning is based on the [official OpenAI model catalog](https://developers.openai.com/api/docs/models) (checked 2026-09-08).

Eastern Time is accepted, with `America/Detroit` as the proposed IANA identifier. The scheduling week starts Monday at 00:00 Eastern. The host platform is macOS. Private in-app day/week/agenda calendar views are V1; calendar export is explicitly deferred to a near-term roadmap follow-up.

## Approval gate

Before implementation, present the proposed plan, contracts, scope boundary, and open decisions for user confirmation. Proceed to implementation only after that confirmation. This gate exists because the user requested it.

## Git and GitHub Workflow

Codex owns the normal Git and GitHub development lifecycle for this repository, including branch creation, commits, pushes, pull requests, review, and merging.

The goal is to allow autonomous development while keeping `main` stable, history clean, changes reviewable, and releases intentional.

### Core principles

- `main` is the protected integration branch and should remain in a known-good state.
- Never develop directly on `main`.
- Never commit directly to `main`.
- Never force-push `main`.
- Every logical change should be developed on a dedicated short-lived branch.
- Every change entering `main` must go through a pull request.
- Pull requests should be squash merged unless explicitly instructed otherwise.
- Codex may autonomously create, review, and merge pull requests when all requirements in this document are satisfied.
- Do not treat successful implementation as sufficient evidence that a change is ready to merge. Implementation and review are separate phases.

### Starting work

Before modifying code:

1. Inspect repository status.
2. Determine the current branch.
3. Fetch the latest remote state.
4. Ensure the work begins from the current `main`.
5. Do not discard or overwrite unrelated uncommitted user changes.
6. Create a dedicated branch for the task.

Use branch names that describe the logical change:

- `feat/<description>`
- `fix/<description>`
- `refactor/<description>`
- `docs/<description>`
- `test/<description>`
- `chore/<description>`

Do not use generic branch names such as `codex-work`, `changes`, `new-stuff`, or `fixes`.

When concurrent tasks are being performed, prefer separate Git worktrees or otherwise isolated branches so tasks cannot modify one another's working trees.

### Scope discipline

A branch and pull request should represent one coherent logical change. Do not include unrelated cleanup, formatting, refactoring, dependency updates, or opportunistic changes unless necessary for the requested work.

If a materially separate problem is discovered, do not silently add it to the current PR; create a separate issue or clearly report it, and use a separate branch/PR if fixing it is appropriate.

Before every commit, inspect:

```bash
git status
git diff
git diff --staged
```

Confirm that only intended files and changes are included.

### Commits

Codex may create commits autonomously. Make checkpoint commits at meaningful milestones when they improve recoverability or isolate meaningful work.

Prefer clear Conventional Commit-style messages where practical, for example `feat: add program search`, `fix: handle overnight schedule boundaries`, or `docs: clarify installation procedure`.

Intermediate branch history does not need to be perfectly curated because PRs are normally squash merged. Do not rewrite published branch history without a specific reason. Never force-push a shared branch unless explicitly instructed or required to safely repair branch history.

### Testing before push

Before claiming implementation is complete:

1. Run tests relevant to the changed code.
2. Run repository-wide tests when practical and appropriate.
3. Run configured linting and type checking.
4. Run build or packaging checks when relevant.
5. Inspect failures rather than assuming they are unrelated.

If an applicable check cannot be run, document exactly which check was not run and why; do not represent it as passing. Do not modify tests merely to make an incorrect implementation pass.

### Pushing

Codex may push task branches autonomously after meaningful checkpoints and before opening or updating a pull request. Never push commits directly to `main`.

If the remote branch has unexpected commits or has diverged, inspect the difference, preserve other contributors' work, reconcile deliberately, and do not overwrite the remote branch merely to make the push succeed.

### Pull requests

Codex should create a pull request for every change intended for `main`. Its description should concisely include:

- **Summary:** what changed and why.
- **Validation:** tests, linting, builds, manual checks, or other validation performed.
- **Risk:** meaningful regression risk, migration implications, compatibility concerns, or areas needing attention.

Do not open multiple PRs for successive corrections to the same logical task; update the existing PR. A draft PR may be opened early when useful, but it must be converted to a mergeable state only after implementation is complete.

### Independent review phase

After implementation is complete, Codex must perform a separate review pass before merging. Treat this as code review, not continuation of implementation, and review the complete PR diff against `main`, not merely the most recently edited files.

The review must look for incorrect or incomplete behavior, regressions, unintended changes, edge cases, error handling, data-loss or concurrency risks, security issues, credential exposure, compatibility, API/schema changes, unnecessary complexity, dead code, dependency changes, insufficient tests, misleading documentation, debugging code, and generated files that should not be committed. Compare the final implementation with the original task requirements. Do not assume code is correct merely because Codex wrote it.

### Review findings

Classify substantive findings as:

- `critical`: security issue, data-loss risk, severe corruption, or fundamentally incorrect behavior.
- `high`: likely regression or major functional defect.
- `medium`: real defect or important maintainability problem that should be corrected before merge.
- `low`: minor issue that does not materially affect correctness.
- `informational`: optional improvement or observation.

Before merge, resolve all critical and high findings; normally resolve medium findings; and intentionally accept any unresolved low or informational findings. After corrections, rerun affected checks, inspect the new diff, review the corrections, and ensure they caused no new problems.

### Recording review status

Because a PR author cannot formally approve their own GitHub PR, do not rely on GitHub self-approval as the review gate. After a passing independent review, add a concise PR comment or equivalent record:

```text
Automated review: PASS

Reviewed the complete diff against main after implementation.

Validation:
- Tests: PASS
- Lint: PASS
- Type check: PASS
- Build: PASS

Blocking review findings: none.
```

Only report checks that actually ran successfully. If blocking findings remain, record `Automated review: BLOCKED` and do not merge.

### Merge requirements and method

Codex may autonomously merge only when work is complete and in scope; the branch is mergeable with current `main` without unresolved conflicts; required GitHub checks and applicable local validation pass; independent review is complete; no unresolved critical or high finding remains; any accepted medium finding has documented safety rationale; no secrets or sensitive configuration are committed; no accidental binaries, build artifacts, local environment files, or unrelated generated content is included; and the change is reasonably safe for `main`.

Do not bypass failing branch protections or required checks, or use administrator privileges to bypass a quality gate. Use squash merge by default, with a clear message such as `feat: add program search and filtering (#84)`. After a successful merge, delete the remote task branch and disposable worktrees when appropriate, update the local view of `main`, and verify the PR actually merged.

### GitHub auto-merge

Prefer GitHub auto-merge when required checks are still running. Once implementation and independent review are complete, Codex may enable squash auto-merge rather than repeatedly polling GitHub. Do not repeatedly poll CI at short intervals solely to discover completion.

### Merge conflicts and failure policy

If `main` changes while a PR is in progress, fetch current `main`, inspect and reconcile conflicts intentionally, rerun applicable validation, and review the resulting complete diff again. Do not blindly choose either side or merge merely because Git reports conflicts resolved.

Do not merge when tests, required CI, or builds fail; a blocking review finding remains; expected behavior cannot be validated; implementation materially differs from the task; the diff has unexplained changes; conflicts cannot be confidently resolved; or unavailable infrastructure prevents meaningful validation. Leave the branch and PR safe and document the blocker. Difficulty or elapsed time never weakens merge criteria.

### Repository protection

Repository configuration should normally require PRs before merging to `main`, required CI/status checks, no direct or force pushes to `main`, and branch deletion after successful merge where appropriate. Human approval is not required for routine autonomous Codex PRs unless repository policy explicitly requires it.

The quality gate is branch isolation, automated validation, independent Codex review, repository status checks, pull-request visibility, protected `main`, and squash merging.

### Versioning and release authority

Use Semantic Versioning (`MAJOR.MINOR.PATCH`): PATCH for backward-compatible bug fixes, MINOR for backward-compatible functionality, and MAJOR for incompatible or intentionally broken compatibility. While pre-1.0, use `0.x.y` releases as appropriate. Version numbers represent releases, not commits, branches, PRs, or merges.

Ordinary feature and bug-fix tasks may be merged but must not automatically create a release. Unless explicitly asked to prepare or publish a release, do not change the public version, create tags or GitHub Releases, publish packages, upload artifacts, or deploy production solely because a PR merged.

When explicitly instructed to release, determine the semantic version from changes since the last release; update version metadata consistently; run release-level validation; merge needed release changes through the normal PR workflow; tag the intended `main` commit with an annotated version tag unless project conventions differ; create and verify the corresponding GitHub Release; and produce concise notes based on actual merged changes.

### Destructive or exceptional Git operations

Treat force pushing, deleting unmerged branches with unique work, resetting shared branches, rewriting published history, deleting tags or releases, reverting multiple unrelated commits, modifying branch protection, and bypassing required checks as exceptional. Do not perform them merely to make the repository appear clean or to unblock a task. Preserve recoverability and existing work.

### Definition of done

A normal coding task is done only when the requested behavior is implemented, relevant validation passes, unintended changes are removed, the complete PR diff undergoes independent review, blocking findings are resolved, the task branch is pushed, the PR accurately describes the change, required checks pass, and the PR is squash merged into `main`—or auto-merge is successfully enabled and GitHub owns the remaining merge gate—with final repository state verified.

If any condition cannot be met, report the task as blocked or partially complete rather than silently weakening requirements.
