# Audit validation record

Date: October 14, 2026 (America/Detroit). Status: audit evidence, not acceptance of
the proposed product changes. Baseline application commit: `65c6ab6`.

## Existing application

- Synthetic database and loopback server isolated under
  `/private/tmp/channeldex-audit-20261014`; no configured devices or live jobs.
- Catalog-only save: reusable source `audit-station-id.mp4`, unknown runtime,
  no local path, no device; save succeeded and returned to Library.
- Missing source and no media: both represented in Library while premiere actions
  remained available. Preparation with no destination exposed the recovery gap U05.
- Weekly preview: Oct 21 premiere followed by Oct 26 replay; second episode Oct 28
  followed by Nov 2. No source readiness prerequisite was imposed on preview.
- Current Library at 390×844: document scroll width 390; no horizontal page overflow.
  Large header/status/navigation consumes much of the first viewport. At desktop,
  staggered filter starts reproduce the supplied screenshot.
- Keyboard spot check: Tab from Library search reaches Show selector, with a visible
  3px outline. Shared source has focus-visible styling but no skip link.
- Today, workbench, Schedule day/week, Shows list/detail, bulk preview, cycle review,
  delivery empty state, History empty state, Settings and Help were inspected live.
  Failed preparation, stale tabs, and workspace isolation received source/test coverage,
  not a fresh full manual simulation.

Behavioral checks (no hardware):

```sh
env PUBTV_DATA_DIR=/private/tmp/channeldex-audit-20261014 .venv/bin/python manage.py test \
  tests.test_ux_overhaul tests.test_library_ingestion \
  tests.test_workflow_queue_ui tests.test_todo_workflows --noinput
# 92 passed, 2.697 seconds

env PUBTV_DATA_DIR=/private/tmp/channeldex-audit-20261014 .venv/bin/python manage.py test \
  tests.test_media_queue tests.test_channel_workspaces --noinput
# 47 passed, 0.519 seconds
```

Both runs reported zero Django system-check issues. Both emitted a non-failing
missing collected-static-directory warning for the isolated data path. Test databases
were created and destroyed by Django; operational records were not touched.

## Prototype scope

`proposal.html` is a standalone local design artifact with no network dependencies,
backend calls, durable storage, or real device actions. Refresh resets the sample.
Its controls demonstrate selected transitions; they do not implement the proposed
production workflow. Weekly cards are selected examples, not a complete 24-hour plan.
History/Settings/Shows are not fully mocked. Filter layout is not a production search.
Intake deliberately uses one fixed Harbor Lights episode so preparation and planning
preserve record context. Other intake purposes are specified in the report rather than
simulated with misleading destinations. Matching-source review is explicitly simulated;
typing or saving a path never counts as real file validation.

The report's proposed contract governs future implementation. Unrepresented cases
(No program, older episodes, carry-forward review, duplicate submission, stale records,
multi-destination qualification) remain required acceptance cases, not capabilities
certified by clicking through the prototype.

## Prototype checks completed

- Browser checked fixed Harbor Lights intake → named catalog confirmation → same
  preparation context. Saving moves focus to the confirmation; cancelling restores
  Add media. Source metadata save does not clear missing-file preparation state.
- Retry is disabled before explicitly simulated matching-source review. After review
  and retry, Today moves that item from Open actions to Waiting; it does not become Ready.
- Premiere confirmation updates Wednesday and Thursday replay, preserves Monday's
  previous-cycle episode, and removes the corresponding daily action. Changing the
  selection hides confirmation and reopens the actionable daily row.
- At 390px with 200% root text (32px), Today, Media, Preparation, and Schedule have
  390px document width. Preparation control and grid overflow found during review
  were corrected. Weekly content scrolls within its 326px region (806px content);
  the region is keyboard-focusable. Media filters share left/right alignment.
- Desktop and narrow screenshots inspected. Preview navigation and the four main
  screens rendered in the browser. These are layout/interaction spot checks, not a
  full accessibility conformance claim.
- Independent review initially requested changes for intake context loss and
  save/cancel focus handling. Both were corrected and browser rechecked.
- Final independent review: **PASS**, no remaining critical, high, or medium findings.
  HTML parsing, unique-ID, local-link, no-network/storage checks and staged whitespace
  checks passed. Final prototype browser error log was empty.

## Limits of validation

No live encoding, FTP, publication, activation, real media validation, migration,
packaging, or hardware qualification occurred. No screen-reader study, exhaustive
contrast audit, populated-history walkthrough, or end-user comprehension study occurred.
Current-app 200% zoom across all screens remains unverified; prototype enlargement
is a separate check and cannot certify the application. No new automated application
tests were added because the change contains documentation and a demonstration only.

Owner walkthrough is still required before product implementation. The owner should
receive a new episode, plan it before preparation is ready, resolve an unavailable
source, and identify the correct replay cycle without coaching. Record confusion as
a finding even when the interaction technically succeeds.
