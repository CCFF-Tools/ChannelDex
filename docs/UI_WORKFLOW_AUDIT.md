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

## Finding-by-finding closure

**Accepted — 2026-10-12:** the owner requested reconciliation of every original
proposal, including implementation gaps omitted from the initial completion
claim. This table supersedes that claim and the original report's proposed/open
status. Historical recommendations remain useful context, not an untracked
backlog. Each row describes its final disposition.

| ID | Implemented outcome | Evidence in repository |
|---|---|---|
| 01 | Workbench navigation uses the station-local day. | `views.py`; `test_ux_overhaul.py` |
| 02 | Ordered upcoming planned entries exclude rescheduled replacements; a separate, discoverable show-specific view retains past plans. | `show_detail.html`; `test_ux_overhaul.py` |
| 03 | Reviewed automatic carry-forward replacement preserves superseded assignments and occurrence history; explicit plans and No program remain protected. | `premiere_planning.py`; `test_bulk_premiere_planning.py` |
| 04 | Show queue and bulk planner share pending episode order. | `services.py`; `test_ux_overhaul.py` |
| 05 | Counts, attention results, and library use effective preparation states, including stale readiness and failures on another destination; hidden/cancelled history is excluded from attention. | `media_queue.py`, `views.py`; `test_media_queue.py` |
| 06 | Device list reaches detail editing and connection configuration. | `device_list.html`; `test_ux_overhaul.py` |
| 07 | Copy distinguishes recorded devices from configured adapters. | `device_list.html`, `settings.html` |
| 08 | Catalog intake and scheduling work without a configured adapter or transfer. | `forms.py`, `media_library.html`; `test_channel_workspaces.py` |
| 09 | Library intake is primary; preparation and specialized guided delivery start from recorded media context. Guided delivery validates and reuses the selected encoded asset. | `guided_delivery.py`, `media_library.html`; `test_guided_delivery.py` |
| 10 | Destination choices display device, owning channel, and adapter; ambiguous choices require selection. | `forms.py`, media and delivery templates; `test_guided_delivery.py` |
| 11 | Media navigation defaults to the library, with preparation activity secondary. | `base.html`, `media_library.html` |
| 12 | Reusable media can be browsed, edited, relinked, filtered, and scheduled. | `views.py`, `media_library.html`; `test_library_ingestion.py` |
| 13 | Today distinguishes reserved time from planned content and coverage. | `dashboard.html`; `test_ux_overhaul.py` |
| 14 | Capacity remains the explicitly named week default; Scheduled items is a separate view. | `calendar_week.html`; `test_ux_overhaul.py` |
| 15 | Calendar controls appear only where applicable and preserve selected dates/modes. | `schedule_controls.html`; `test_ux_overhaul.py` |
| 16 | Edit actions use edit headings, object context, and return links. | `occurrence_form.html`; `test_ux_overhaul.py` |
| 17 | Duration entry uses hours/minutes/seconds; review uses readable labels and distinguishes unknown values. | `forms.py`, `media_queue.html`; `test_channel_workspaces.py` |
| 18 | Received media, preparation, transfer, scheduling, and airing use distinct action labels. | episode/media/workbench templates; `test_end_user_documentation.py` |
| 19 | Ordinary readiness uses plain language; integration-specific details stay in their relevant workflow/evidence context. | `help.html`, `guided_delivery.html`; `test_todo_workflows.py` |
| 20 | Help points to available configuration and contextual evidence actions; History remains read-only. | `help.html`; `test_end_user_documentation.py` |
| 21 | Unknown and recorded zero remain distinct for future entry. Owner-confirmed historical unknown values were corrected with backup and audit records. | `forms.py`; `test_channel_workspaces.py`; data disposition below |
| 22 | Shadowed routes removed; deliberate compatibility handlers retained. | `urls.py`; `test_ux_overhaul.py` |
| 23 | Titles, form returns, duration/date labels, Agenda labels, settings scope, and error recovery are consistent. | shared/form/calendar/error templates; `test_ux_overhaul.py` |
| 24 | Runtime guidance describes implemented capabilities; historical decisions remain historical. | `README_RUNTIME.md`, `ARCHITECTURE.md`, this closure record |
| 25 | Workspace switching handles nested editors, creation links, and unknown routes safely. Show/time-slot forms inherit the channel without a duplicate selector; explicit foreign submissions are rejected. Error pages offer recovery. | `active_station.py`, `forms.py`, error handlers in `views.py`; workspace/UX tests |
| 26 | Draft storage uses station IDs and restores only valid workspace choices. | `media_intake.js`; media-intake state checks |
| 27 | Device ownership is visible; owner-confirmed GOV-TV ownership was corrected after checking linked records. | `device_list.html`; data disposition below |
| 28 | Consistent destination identity and single/multiple choice behavior within the inherited workspace. | `forms.py`, destination templates; `test_guided_delivery.py` |
| 29 | Fallback is announced; originating workspace is validated; stale tabs preserve input and block writes until recovery. Jobs retain their original targets. | `middleware.py`, `base.html`; `test_channel_workspaces.py` |

## Implementation and validation

The initial implementation was reviewed on 2026-10-11. Its completion statement
was too broad: the 2026-10-12 reconciliation found remaining contextual-delivery,
effective-readiness, nested-navigation, past-plan, and presentation gaps. The
closure changes address those omissions. Initial validation was:

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

**Closure validation — 2026-10-12:** after the final corrections, the complete
affected manifest passed **224 tests with zero failures or errors**. It covers
channel workspaces, guided delivery, media queue/library ingestion, UX,
documentation, schedule preparation, UltraNEXUS workflows, core scheduling,
content workflows, usability, and data foundations. Django system checks,
migration consistency (no new migration), media-intake JavaScript state checks,
dependency consistency, and diff checks passed. A fresh isolated PyInstaller
build passed without replacing the installed app. No separate lint/type-check
tool was available.

The final synthetic-data browser walkthrough verified workspace recovery from
a missing page and a nested editor, separate past plans, the exclusion of
superseded upcoming duplicates, accurate show titles, and removal of the
redundant Station selector. Independent review of the complete final diff
passed with no remaining substantive findings. All 29 audit findings are
closed; no audit implementation TODO remains.

## Decisions and data corrections

**Accepted and completed — 2026-10-12:** the owner confirmed that the GOV-TV-named
device belongs to GOV-TV and that the three existing zero-runtime values mean
unknown. A consistent database backup was saved before correcting the device's
ownership and setting those runtimes to null. The device had no linked
configuration or evidence; the runtime records had no linked scheduled
occurrences, and the asset had no preparation batch. Each of the four changes
has its own audit record. Post-change ownership, values, audit count, and foreign
key integrity were verified. Private record details and the backup are not
committed to this repository. No broad zero-to-null migration was introduced.

Other original open decisions are resolved: attention is scoped to visible,
non-cancelled work in the active workspace using effective preparation state;
Week retains the explicitly labeled capacity default; carry-forward replacement
is supported directly in the reviewed bulk planner. No successor platform or
generic adapter framework is required by this audit. App-wide database controls
are explicitly labeled separately from channel settings.

The audit has no remaining proposed feature or data-repair decision. Existing
hardware qualification and future-platform work are separate scope, not
unimplemented audit findings.

Live hardware qualification remains separate from this UI work. No release,
version bump, production deployment, or device operation is authorized solely
by merging these changes.
