# ChannelDex workflow and usability audit — October 14, 2026

**Accepted scope:** audit the whole application, weighted toward receiving media,
preparing it, and placing it into the weekly schedule; evaluate daily action guidance.
**Proposed:** all redesigns and contracts below. Approval of this audit does not approve
their implementation. The audit itself changed no application behavior, operational records, or hardware.

**Subsequent accepted direction:** the owner separately authorized product implementation. See [implementation status and validation](implementation.md); the findings below remain the design contract.

## Finding

ChannelDex exposes individual operations more clearly than it explains the job that
connects them. The operator must reconcile several meanings of readiness, choose
among overlapping planning entry points, and infer what to do after saving. Alignment
problems amplify that cognitive load; correcting CSS alone will not resolve it.

The strongest recommendation is a connected media workspace with independent
**Preparation** and **Schedule** states, a weekly plan that shows actual selections,
and a Today list that links directly to the next unresolved action. Do not turn the
workflow into a mandatory linear wizard: cataloging and planning must work without
hardware, and an episode can be scheduled before preparation is finished.

Open [the annotated interactive proposal](proposal.html) in a browser. Its four main
screens are Today, Media, Preparation, and Schedule; Shows is an explanatory reference.
All records and simulated changes in that file are illustrative, held only in page memory.
It is a discussion prototype, not the application or a full interaction specification.

## Evidence and coverage

Baseline: `65c6ab6` (`fix: close remaining UI audit workflow gaps (#28)`). Source
inspection and synthetic-data browser walkthrough performed October 14, 2026,
America/Detroit. No real producer submissions, credentials, or operational database
were used. The existing untracked `UI Mockups/` directory was preserved.

The isolated Django instance contained a show, two pending episodes, a missing source
reference, Wednesday premiere and Monday replay times, and a manually planned episode.
There were no configured devices. This deliberately exercised catalog-only operation
and incomplete preparation, not a successful hardware delivery.

| Area | Evidence obtained | Limit |
| --- | --- | --- |
| Library/intake | Browser: created a reusable source record with no path or device; returned to Library with generic success message. Inspected episode actions and missing-media state. | No real files copied or inspected. |
| Preparation | Browser: followed an existing episode asset into preparation; empty destination selector, disabled Show, no configuration recovery link. Source: reviewed intake/confirm/retry paths. | Encoding/transfer and failed worker states were not exercised against hardware. |
| Today/workbench | Browser: Today showed one attention item from missing upload coverage; workbench offered several evidence actions and separate state summaries. | Not an operator usability study. |
| Week/planning | Browser: Week opened capacity-only; episode titles hidden. Bulk preview accepted unprepared/missing media and showed Oct 21 premiere → Oct 26 replay. | Preview inspected, no actual cycle confirmation needed for this audit. |
| Review/delivery | Browser: review showed no confirmed cycles despite a manually scheduled item; delivery showed empty publication state and advanced qualification disclosure. | No publication, upload, activation, or independent confirmation performed. |
| Shows/history/settings/help | Browser: inspected show queue, planning choices, empty History, settings grouping, and help terminology. Source: inspected context propagation and shared navigation. | Populated history and every settings form were not manually exercised. |
| Visual/accessibility | Browser: reproduced staggered Library fields at desktop size; checked Library at 390×844 with no horizontal document overflow; keyboard Tab reached Show with visible 3px focus outline. | This is a spot check, not WCAG certification or full assistive-technology testing. |

The earlier audit's closure record remains historical. Its 29 resolved findings do
not establish that a new user can complete a whole job without confusion. This report
adds a new audit scope rather than silently reopening or rewriting that record.

## Prioritized findings

Severity reflects operator impact, not a security classification. **Observed** means
browser and/or source evidence; **hypothesis** is a design judgment to validate with
the owner. Source line references are against the baseline above.

| ID / priority | Finding and impact | Evidence | Proposed correction |
| --- | --- | --- | --- |
| U01 — High | **Observed:** intake, destination selection, review, confirmation, job activity, and retry share the preparation page. Starting work and monitoring existing work compete. | `pubtv/templates/media_queue.html:6–24`; `pubtv/operations/media_queue.py:603–836` | Keep one Add media entry in Media. Put contextual preparation setup/review in a focused flow; make Preparation activity primarily a monitor. Preserve historical connected-intake compatibility without presenting it as another normal route. |
| U02 — High | **Observed:** Today’s “Needs attention” count is the number of planned items without active upload coverage, not a consolidated actionable work list. It misses unscheduled intake work and conflates distinct blockers. | `pubtv/operations/views.py:216–260`; `pubtv/templates/dashboard.html:7–9`; live walkthrough | Replace the generic counter with reasoned action groups; keep upload coverage as a specifically named fact. Use the daily-action contract below. |
| U03 — High | **Observed:** Week defaults to capacity and intentionally hides assigned episodes. This conflicts with using Week to answer “what will run?” **Hypothesis:** planned content should be the default. | `pubtv/operations/views.py:1830–1873`; `pubtv/templates/calendar_week.html:7–18`; live screenshot | Propose Scheduled week as the default, retaining Capacity as an explicit secondary view. This changes a previously accepted default and requires owner approval. |
| U04 — High | **Observed:** “Review schedule” does not retain the visible calendar date/week and selects confirmed premiere/rerun cycles, not every item in the day/week. A user can see scheduled content and then “No confirmed premiere cycles.” | `pubtv/templates/schedule_controls.html:8–23`; `pubtv/operations/schedule_preparation.py:205–236`; live walkthrough | Name the existing tool “Review cycle delivery.” Add a separate whole-week planning review, preserving selected dates and identifying excluded delivery items. Do not imply that live/filler/manual entries are automatically supported by cycle delivery. |
| U05 — High | **Observed:** with no device, preparation exposes an empty required dropdown and disabled Show, without a local recovery action. | `pubtv/templates/media_queue.html:9–15`; synthetic no-device browser case | Explain “No destination configured”; offer Configure destination and Return to media/Plan episode. Do not make configuration a prerequisite to cataloging or planning. |
| U06 — Medium | **Observed:** Library filter inputs start immediately after labels of different lengths. `display:block` applies to the wrapping label, not a separate label row; inline controls retain their own width cap. This reproduces the supplied screenshot. | `pubtv/templates/media_library.html:7`; `pubtv/static/pubtv.css:7`; desktop and narrow browser checks | Dedicated filter grid: label above control, shared alignment and height, search wider than selects, one aligned Apply filters action. Stack on narrow windows; do not globally rewrite all form layout. |
| U07 — Medium | **Observed:** Preparation activity appears as a heading action and a tab, with preparation status links above both. Library cards also expose relink, edit, prepare, and planning together. | `pubtv/templates/base.html:6`; `pubtv/templates/media_library.html:5–11` | One secondary Preparation destination; compact status summary when relevant. One state-appropriate primary action per row; keep other valid actions accessible in details. Reveal relink primarily for missing paths or an explicit location change. |
| U08 — Medium | **Observed:** Add media from a show passes a return URL but not the show/episode selection; the generic form starts with an unfiltered optional episode choice. A new episode must be created elsewhere. | `pubtv/templates/show_detail.html:5`; `pubtv/operations/views.py:1540–1544`; asset form walkthrough | Carry validated show context into intake; distinguish new episode, existing episode, and reusable media up front. Offer inline episode creation with a reviewable catalog save. |
| U09 — Medium | **Observed:** Library save returns a generic “Add media file saved” message. It does not focus the saved record or give a contextual Prepare/Plan choice. | Live catalog-only save; `pubtv/operations/views.py:1540–1544` and shared form-save behavior | Confirmation names the record and says “Saved to library.” Provide independent Prepare and Plan actions and a return-to-list option; retain incoming filters. |
| U10 — Medium | **Observed:** Library uses `pending`/`missing`, Shows uses workflow and queue labels, Today shows “0 of 6 preparation steps recorded,” and the workbench separates upload/airing. Users must translate them. | `media_library.html:10–11`; `dashboard.html:9`; `show_detail.html`; `occurrence_workbench.html` | Shared state vocabulary with separate dimensions: source, destination preparation, schedule assignment, delivery evidence. Never reduce these to one overall Done badge. |
| U11 — Medium | **Observed:** Show detail exposes seven heading actions plus another premiere-planner link; single and multiple premiere planning have different entry points. | `pubtv/templates/show_detail.html:5`; browser show detail | One Plan premieres action supporting one or many episodes; retain producer-directed queue order, explicit No program, older-episode selection, and reviewed carry-forward replacement. Put configuration under show details. |
| U12 — Medium | **Observed:** primary navigation supports active state and CSS supports focus-visible, but there is no skip-to-content link; History tabs lack a named group/current selection. | `pubtv/templates/base.html:4–8`; `pubtv/templates/history.html:1`; `pubtv/static/pubtv.css:8` | Add skip link, consistent current-page semantics, action-result announcements, and focus placement after review/error transitions. Keep native keyboard controls. |
| U13 — Low | **Observed:** Settings repeats workspace/application headings, and Help calls a day-schedule link “Today,” which differs from main navigation Today. | `pubtv/templates/settings.html`; `pubtv/templates/help.html`; browser inspection | Group Settings once by scope; standardize Today versus Day plan, Review cycle delivery versus Week review, and media preparation versus schedule delivery. |

## Current and proposed workflow maps

**Observed current flow** — each arrow is a navigation or decision handoff, not a
claim that all steps are mandatory.

```text
Shows → Add episode → Add media file ────────────────┐
Media Library → Add media file → generic list return ├→ locate record again
Preparation activity → destination + intake → review┘
  → confirmation → queue / worker states

Library → select episodes → bulk premiere preview → confirm cycles
Shows → choose next premiere OR plan multiple premieres → confirm
Schedule → Week capacity → switch view to see planned items
  → Review schedule (cycle selection; date context not carried)
  → schedule delivery / separately recorded upload evidence

Today → uncovered planned item → workbench → choose an evidence/action path
```

**Proposed flow** — the same record stays in context; preparation and planning branch
independently. Lines reconverge for review, not to impose a preparation-first gate.

```text
Media → Add media → identify episode/reusable purpose + source reference
  → Save to library → named-record confirmation
       ├→ Prepare for destination → review intended work → queue → monitor/recover
       └→ Plan premiere / place reusable media → review exact dates → save plan
                     ↓                                  ↓
              Weekly plan: content + preparation gaps + explicit unassigned times
                     ├→ Whole-week planning review (all item types)
                     └→ Review cycle delivery (supported subset + destination)
                              → existing guarded delivery / evidence workflows

Today actions → specific unresolved condition → contextual repair or planning
  → condition re-evaluated → resolved, still waiting, or blocked with explanation
History ← attributed evidence; never inferred from queue completion or planned content
```

## Proposed information architecture and screen contract

- **Today:** actionable work first; today's timeline below it. Upcoming work includes
  the current and following scheduling weeks, plus older unresolved exceptions in a
  separate section. Label the selected horizon instead of silently mixing dates.
- **Media:** Library is the default. Add media is the sole normal intake entry;
  Preparation is a secondary view. A record shows show/episode identity, local source
  availability, destination-specific preparation, next planned use, and one recommended
  action. A missing source does not disable Plan.
- **Schedule:** proposed default Week/Planned items; retain Day, Agenda, Recurring
  times, and Capacity. Show reservations, explicit No program, planned content, known
  virtual-channel intervals, and unknown coverage distinctly. Fix times remain fixed;
  overlong runtime is a warning, never an automatic shift or trim.
- **Shows:** producer metadata, recurring times, intended episode order and explicit
  premiere choices. It remains a management workspace, not just documentation.
- **History, Help, Settings:** retained utility destinations. Move routine recovery
  links into the relevant screen; do not require searching Help for the next step.

Every screen should answer: What record/date am I working on? What is its state?
What is the next useful action? What changed when I saved? Maintain channel, show,
episode/asset, selected week, and safe return context across handoffs. Revalidate all
context on submission; do not weaken stale-tab or cross-workspace protections.

### Intake and preparation

Use purpose (new episode/existing episode/reusable), minimal identity, and source
reference first. Keep submission provenance distinct from storage location. Allow
unknown runtime. Technical IDs, prior renditions, and detailed preparation evidence
belong in details. A catalog save does not inspect or transfer the file.

Preparation starts with the chosen asset, then a deliberately selected or unambiguous
destination. Show the intended encoding/verification/transfer actions in review.
Confirmation means queued, not ready. Retain token idempotency, source-change review,
destination qualifications, and cancellation/history semantics. Missing configuration
gets a concrete recovery path; waiting jobs do not look like operator tasks to retry.

### Weekly planning and review

Choose one or more episodes in producer order and an actual premiere date; preview
each premiere and linked replay date before saving. A Monday replay before Wednesday's
premiere belongs to the previous cycle. Preparation may be unfinished. Show the next
planned use in Media and provide a reciprocal link from Schedule.

A whole-week review summarizes 24-hour coverage, conflicts, overruns, unknown runtime,
unassigned reservations, and destination readiness. An intentional virtual-channel
interval is not an unknown gap. Existing cycle delivery remains explicitly scoped;
unsupported manual/live/reusable items remain visible with their handling path, not
silently omitted. No automatic scheduling, queue advancement, or airing inference is
introduced by this redesign.

## Proposed daily-action contract

These are derived views of current records, not a second authoritative task database.
No manually checked Done state should override the underlying preparation or schedule.

| Source condition | Display/action | Resolution condition |
| --- | --- | --- |
| A planned asset has a missing/changed local source needed for unfinished preparation | “Locate source for [episode]”; date/next use; Open media location | Correct source is reviewed under existing identity rules, or a reviewed alternative is selected. Merely typing a path does not prove identity. |
| Active preparation is failed, blocked, or stale | Explain blocker and destination; Review source/configuration or Retry when allowed | Effective state no longer requires intervention. Queued/running becomes Waiting, not Done/Ready. |
| An upcoming reserved premiere lacks an explicit or valid generated choice | “Choose episode for [show/date]”; Open planner | Valid episode or explicit No program choice recorded. A generic manual occurrence at the same time must be surfaced for reconciliation, not silently treated as the cycle assignment. |
| A planned item has unknown runtime, exceeds its fixed slot, or conflicts | Explain exact item and affected time; Review plan/media | Relevant field or plan is corrected, or an existing explicit reviewed disposition resolves it; never invent an automatic override. |
| A selected week has genuinely unknown coverage | “Review unplanned time”; Open day at interval | Coverage receives an explicit item/classification. Known virtual-channel return and intentional intervals are not errors. |
| A current/future plan lacks matching destination preparation or required schedule upload coverage | Separate “Prepare media” / “Review delivery” actions with destination and revision context | Existing readiness/coverage rules satisfied for the selected destination and exact revision. Uploaded does not mean aired. |
| Past premiere is cancelled, known failed, or lacks evidence required for queue classification | “Review past premiere”; show evidence gap | Owner resolves through existing audited workflow. Never claim failure to air just because upload evidence is absent. |
| Unscheduled catalog item is incomplete | Lower-priority Library work, with “No scheduled date” | Relevant metadata supplied or deliberately left optional; no invented deadline. |

**Identity and deduplication:** derive a stable key from station, reason, underlying
asset/occurrence/assignment, and destination where applicable. Group one asset needed
by several occurrences into one preparation task, listing affected uses. Keep distinct
destinations separate. Count actionable groups, not raw warning instances.

**Ordering:** today's unresolved schedule-impacting blockers first; next dated work
in the selected horizon second; past exceptions requiring owner review separately;
unscheduled housekeeping last. Within each group sort by next affected local start,
then stable identity. Show urgency in words and dates; do not invent preparation lead
times or deadlines. Retrying an item must not reset its relevance or hide its history.

**Lifecycle:** actionable → waiting → resolved is recomputed from evidence. A stale
revision/source can reopen work. Failed attempts stay visible with cause and recovery;
cancelled/removed batches leave the active-job list but do not erase unmet schedule
requirements. Completion of preparation does not complete planning or upload work.
Never generate an “airing failed” task solely from absent airing evidence.

**Navigation:** every action targets the relevant record, reason, destination, and
date, preserving a safe return to Today. Invalid/deleted/foreign targets produce a
clear explanation and refreshed list. Workspace switching invalidates incompatible
object context. Normal refresh re-evaluates work; unknown/loading status is not zero.

**Manual to-dos:** proposed defer. The observed problems are derived operational work.
Free-form tasks would require an additional model, completion rules, persistence,
and separation from evidence. If the owner needs reminders such as “call producer,”
add that as a later explicitly approved capability, visually separate from system work.

## Remediation sequence and interface implications

All stages below require approval; none is implemented by this documentation change.

| Stage | Change | Interface/data impact | Acceptance gate |
| --- | --- | --- | --- |
| 1 — Shared layout | Aligned filters; remove duplicate links; consistent action hierarchy, active states, skip link, feedback, narrow layout | Template/CSS changes; retain existing route and form behavior | Desktop/narrow/200% visual checks; keyboard completion; no displaced controls or page overflow |
| 2 — Media handoffs | Unified intake, show context, named success, contextual Prepare/Plan, focused preparation review and separate monitor | Reuse asset/episode APIs and existing review tokens; add validated show/return context and minimal inline-episode orchestration if approved. Preserve legacy route compatibility. No schema migration assumed. | Catalog-only, existing/new episode and reusable paths; duplicate submission; missing/changed source; no destination and multiple destinations |
| 3 — Weekly clarity | Planned week default; unified premiere entry; date-preserving whole-week review and clearly scoped cycle delivery | Calendar default changes; existing capacity URL remains; context parameters validated; whole-week review is read-only and separate from delivery mutation endpoints | No program/carry-forward protection; producer order; Monday replay; unknown/zero duration; fixed start/overrun; all-day item types |
| 4 — Today actions | Derived reasons, prioritization, grouping, contextual recovery, waiting states | Shared server-side action projection reused by Today and counts; no durable Task table or completion endpoint for derived work | Deduplication, destination separation, stale revisions, cancellation, workspace isolation, disappeared target, unknown status |
| 5 — Operator acceptance | Owner walks through the proposal, then approved implementation, without coaching | Documentation/help labels aligned with actual UI | Can identify start, next action, saved outcome, preparation vs planned vs uploaded vs aired; record unresolved friction rather than declaring blanket closure |

## Validation and remaining decisions

Existing contract test run:

```sh
env PUBTV_DATA_DIR=/private/tmp/channeldex-audit-20261014 .venv/bin/python manage.py test \
  tests.test_ux_overhaul tests.test_library_ingestion \
  tests.test_workflow_queue_ui tests.test_todo_workflows --noinput
```

**Result: 92 tests passed; Django system check reported no issues.** The isolated
data directory had no collected-static directory, producing a non-failing warning.
These tests support current behavior and protect requirements; they do not validate
the proposed redesign. No application source changed, so a packaging build, new
migrations, and a full application regression run were not needed for this audit.

A separate, non-repeated run of `tests.test_media_queue` and
`tests.test_channel_workspaces` passed **47 additional tests**, covering preparation
retry/effective state and workspace boundaries more directly. Total: **139 passed**
across six modules. The four-module run includes episode-queue reconciliation;
preparation retries are specifically covered by the additional media-queue module.

| Scenario | Evidence now | Required acceptance for remediation |
| --- | --- | --- |
| Catalog only, no hardware, unknown runtime | Browser save + library tests | Named success, independent Prepare/Plan, preserved source provenance |
| Schedule before ready; missing media | Browser bulk preview + library/TODO tests | Plan accepted; missing preparation still visible from Week and Today |
| Failure/retry/stale source | Source + queue tests | Actionable cause; stale source requires review; no duplicate work |
| Episode order, reruns, premiere cycles | Browser Oct 21 → Oct 26 preview + TODO tests | Producer order; Monday prior-cycle label; no rerun queue consumption |
| Unassigned times and No program | Source + existing scheduling tests | Explicit unassigned state; No program honored; intentional coverage not falsely flagged |
| Workspace/error recovery | UX tests + source inspection | Preserve valid dates; drop foreign object IDs; stale submission preserves input |
| Narrow windows and keyboard | Live Library at 390×844; visible focus check | Extend across every redesigned screen, forms, review, and errors |

See [validation notes](validation.md) for prototype interaction and visual checks,
known limits, and independent artifact review results.

**Open for owner approval:**

1. Approve the connected Media workspace and the four-stage remediation direction?
2. Change the accepted Week default from Capacity to Planned items, retaining Capacity?
3. Approve a distinct whole-week planning review and the “Review cycle delivery” name?
4. Start daily actions with derived work only, deferring manually entered reminders?

The design recommendations are not marked accepted until the owner approves them.
Human comprehension remains unverified until that walkthrough occurs; passing tests
and a polished mockup are not substitutes for it.
