# UI and workflow remediation

**Accepted — 2026-10-11:** implement the October 8 UI audit, with the channel
selector acting as a workspace switcher. Prioritize platform-independent media
tracking, management, and scheduling while preserving existing UltraNEXUS
delivery capabilities and their review gates. No replacement platform is chosen.

## Contracts

- The active channel scopes catalog, schedule, history, devices, preparation,
  and drafts. A destination device is a resource within that workspace; it is
  not another channel selector.
- Switching workspaces preserves meaningful calendar context, but drops
  object IDs from the previous workspace. Unsaved forms must not submit into
  a different workspace. Existing jobs retain their original channel/target.
- Catalog intake can save records without configuring hardware or starting
  encoding/transfer. Preparation for a destination is an explicit later action.
- Source and encoded renditions, planned content and reservations, upload and
  airing evidence remain separate facts. Historical provenance is retained.
- Explicit premiere assignments can replace automatic carry-forward through
  review. Explicit assignments and No program choices must not be silently
  overwritten. Pending order must agree across show and bulk-planner views.
- Use station-local dates in navigation and human-readable duration fields.
  Keep the existing capacity view and name it clearly; do not reinterpret
  recorded zero durations or move devices based on their names.

## Audit coverage

The original audit inspected Today, Settings, devices, Media Preparation,
Library, shows, episodes, all schedule views, schedule review/delivery, guided
delivery, Help, History, occurrence workbench/edit, bulk premieres, and asset
creation in the running application. Source inspection covered route wiring,
forms, station scoping, queue queries, and remaining evidence/portability flows.
It did not certify live encoding, FTP, controller activation, or every error
and accessibility state.

The accepted findings are tracked below. Completion requires behavioral
validation and review, not merely a documentation entry.

| IDs | Required outcome |
|---|---|
| 1–2 | Correct station-local breadcrumb dates and genuinely upcoming, ordered occurrences. |
| 3–4 | Reviewed replacement of automatic carry-forward and consistent pending order. |
| 5 | Consistent, explicitly scoped attention/activity/library status. |
| 6–7 | Device list reaches editing/configuration and describes capabilities truthfully. |
| 8–9 | Library-only intake and contextual preparation/delivery entry points. |
| 10 | Deliberate, clear destination identity and consistent selection. |
| 11–12 | Media library is primary, including reusable assets without episodes. |
| 13–16 | Clear reservation/assignment views, preserved calendar context, and correct edit labels. |
| 17–21 | Shared duration entry, precise action terminology, actionable status, accurate Help, and explicit unknown runtime. |
| 22–24 | Canonical routing, consistent presentation, and current runtime documentation. |
| 25–26 | Safe workspace navigation and channel-specific media drafts. |
| 27 | Visible device ownership and an explicit, non-destructive data-correction boundary. |
| 28–29 | Destination choice within the workspace, announced fallback, and protection against stale tabs/forms. |

## Implementation and validation

**Accepted — implemented and reviewed 2026-10-11:** the application changes
above are complete. Finding 27 establishes visible ownership and the correction
boundary below; it does not authorize guessing a device's intended channel.

- The full 426-test run had two failures in expectations for replaced readiness
  wording and no errors. After correcting those expectations and final picker
  context/title changes, all 67 tests in the affected final validation modules
  passed. Earlier behavioral failures were repaired before that full run.
- Django system checks, migration consistency, Python compilation, JavaScript
  syntax and media-intake state checks, dependency consistency, and diff
  whitespace checks passed. No separate lint or type-check tool was available.
- An isolated PyInstaller build passed without replacing the installed app.
- Browser checks on synthetic data verified workspace navigation, catalog-only
  intake, unknown runtime, reviewed carry-forward replacement with retained
  history, station-local dates, and stale-tab protection preserving draft input.
- Independent review of the complete diff passed with no blocking findings.

These checks did not migrate operational data or qualify live hardware.

## Data corrections and external qualification

**Open:** a device name that contains another channel's name does not establish
its intended ownership. Review actual device identity and linked transfer,
publication, programming, and upload records before proposing any reassignment.
UI remediation must not rewrite those records automatically.

**Open:** historical zero durations may represent actual recorded values or
missing information. Preserve them until the owner identifies which records
need correction; new unknown values remain distinct from zero.

Live hardware qualification remains separate from this UI work. No release,
version bump, production deployment, or device operation is authorized solely
by merging these changes.
