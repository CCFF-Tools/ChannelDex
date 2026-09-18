# Decisions and owner review

## Usability and information architecture decision (accepted 2026-09-17)

The owner accepted implementation of the complete usability overhaul from the
2026-09-17 review. The target IA is **Today**, **Schedule** (Day, Week, Agenda,
Recurring Times), **Shows**, **History**, and **Settings**. Contextual actions,
inline supporting-record creation, progressive forms, compatibility redirects,
and return-to-context navigation are part of the contract. Today is the daily
work surface; History is principally read-only. A schedule-item workbench and a
complete Show workspace are the canonical interaction surfaces.

The preparation/replay ownership decision is resolved. Source availability and
encoding are asset-version facts; transfer and library registration are
asset-version + device facts; playback programming is occurrence + device; and
uploaded schedule coverage is an explicit immutable upload-to-occurrence-revision
relationship. Replays reuse asset preparation for the same exact asset version,
but programming and upload coverage are occurrence-specific. Show-level slot
length is the current setting and occurrence revisions retain snapshots.

Canonical Producer, filename, receipt, status, timeline, and label concepts are
used in owner-facing forms. Legacy duplicates remain migration-only metadata and
are not editable user-facing fields. The following distinctions are accepted
invariants and must not be collapsed: plan/airing evidence, reservation/occurrence,
notice/receipt, source/encoded, slot/runtime, preparation/programming/upload,
queue/readiness, and reported/log-verified evidence.

Status: approved for V1 implementation on 2026-09-09; packaging and operational details remain open/deferred. Updated 2026-09-09.

## Confirmed from the owner's request

- Build a portable, self-contained browser-accessible application without required external hosting.
- Initially run on one Mac for one editor, the owner. Public access is deferred; calendar export is out of the first release and a near-term roadmap follow-up.
- Use Eastern Time with a scheduling week starting Monday at midnight (00:00).
- Cover the complete daily schedule, including recurring programs, filler, station IDs, PSAs, and live events.
- Prioritize PUB-TV; preserve a path to GOV-TV later.
- Track recurring weekly show slots, including a premiere and repeat airings of the same episode that week.
- Order unaired episodes by producer direction, usually FIFO; retain intended air order and optional intended premiere dates.
- Record deliveries from Dropbox, email download links, or local SMB storage without treating link arrival as receipt or assuming connector access.
- Replays use the episode from the most recent premiere cycle. A replay after a premiere keeps that episode until the next premiere; a replay before the premiere day in a Monday-based calendar view belongs to the prior cycle. When no new episode is available, the owner may choose an older episode for that cycle case by case.
- Retain show producer contact details, episode history with prior air dates, and the next episode on the slate.
- Include six separately recorded manual preparation facts: source available; AME preset/version/machine/output; exact FTP output/device; WinLGX library registration; slot assignment; and uploaded schedule revision.
- Provide an easy-to-understand private schedule calendar for staff.
- Account for the current Leightronix Ultra-Nexus HD and a possible replacement in about six months; TelVue is only a possibility.
- The owner can provide Leightronix material for schedule seeding or later historical reconciliation. The available file formats and evidence types are not yet known.
- The initial release tracks playlist preparation and media management notes manually. Leightronix file/schedule exchange and machine ingestion are future roadmap work, not prerequisites for the first release.
- Use cost-conscious sub-agents and task-appropriate reasoning. Write guidance and proposals now; confirm plan details before implementing the application.

## Accepted V1 defaults

| Decision | Recommendation | Why it matters |
| --- | --- | --- |
| Application shape | One loopback service and browser on the owner's Mac | Durable personal data without cloud hosting |
| Stack | Python, Django, SQLite; locally served UI assets | One maintainable application and portable database |
| First scope | Scheduling and human-entered operations tracking | Delivers the weekly workflow without tying it to soon-to-retire hardware |
| Episode choice | Queue-derived suggestion per premiere cycle, confirmed by the owner; preview linked replays | Reduces repetitive entry without allowing unexpected substitutions |
| No new episode | Flag a missing assignment; staff choose rerun, hold, or cancellation | Avoids silent reuse or unapproved slate advancement |
| History | Confirmed source-backed airings distinct from plans | Preserves trustworthy prior-air-date records |
| Media | Versioned asset metadata and file references only | Avoids turning the initial release into a media storage/transcoding system |
| Readiness | Received/transcoded per asset; transfer per target; programmed per airing | One file can air several times or on several devices |
| Calendar | Private full-day/week grid and accessible agenda; export deferred to near-term roadmap | Supports V1 planning without public hosting |
| External integrations | Deferred; operator-imported files only after future format validation | Remains usable through the playout replacement |
| Timezone representation | `America/Detroit` station timezone mapping | Implements the confirmed Eastern Time requirement |
| Full-day planning | Explicit timed items; visible gaps and overlaps, manual filler selection | Covers all content without becoming a playout automation engine |

## Open/deferred questions for rollout and roadmap

Core operating decisions are now recorded. Remaining recommendations still need review; a deferred feature does not block the manual first release.

| ID | Decision needed | Suggested starting point |
| --- | --- | --- |
| D01 | **Accepted:** initial runtime, packaging, and testing target is macOS 26.x on Apple Silicon on the development machine. Unsigned local PyInstaller arm64 packaging is implemented and smoke-tested. Intel Mac is a later compatibility-validation target, not yet proven; no universal binary is promised. | Signing, notarization, installation restrictions, distribution form, and Intel validation remain open/deferred |
| D02 | **Resolved:** one owner/editor, one computer; app opens directly without a separate login or password; public access deferred | Loopback only; configured local owner is the audit actor. LAN access, accounts, and public access remain deferred; calendar export is separately deferred by D11 |
| D03 | **Confirmed:** Eastern local wall-clock time; Monday 00:00 week start; seconds precision; ordinary recurring times keep their local clock time year-round | Eastern-time implementation detail; Sunday 01:00–03:00 is curated supplemental filler with no assigned producer slots. Owner closed this issue; no further planning effort or approval blocker |
| D04 | **Confirmed:** replays follow the most recent premiere-to-premiere cycle, independent of the Monday calendar boundary | A Monday replay before a Wednesday premiere uses the prior cycle; the following replays use Wednesday's new episode until the next premiere |
| D05 | **Confirmed:** old episodes chosen case by case when no new premiere | The app suggests the pending queue and next configured premiere, but the owner confirms or explicitly selects an older episode; D14 controls the recorded transition of a passed new-premiere episode to previously scheduled |
| D05a | **Confirmed:** producer-directed episode order, usually FIFO; intended premiere date/order are planning fields | FIFO by recorded receipt timestamp is proposed only as an initial queue suggestion; the owner can directly reorder the visible pending queue, saved changes are audited, manual producer direction wins, and conflicts are flagged |
| D06 | **Confirmed:** full 24-hour schedule, including filler, IDs, PSAs, live events | Explicit timed entries with gap/overlap detection; no automatic filler |
| D07 | **Resolved:** seconds precision; fixed starts even for live overruns; flag content exceeding available slot | No automatic shifting, trimming, filler, or playout control |
| D08 | **Accepted:** minimum identification and optional descriptive/contact fields | Every source or encoded media asset has a stable unique application-generated Asset ID and a separately recorded filename. Source and encoded renditions remain distinct. Episode Title remains separate until the owner-facing identification decision is resolved. Other descriptive/contact fields, runtime, notes, and file references remain optional; unknown runtime, privacy, and provenance rules remain in effect. |
| D09 | **Accepted:** manual checklist facts, N/A handling, and confirmation authority | Record six distinct facts: source available; AME preset/version/machine/output; exact FTP output/device; WinLGX library registration; slot assignment; uploaded schedule revision. The configured owner confirms them. File-related facts may be N/A for live entries; each recorded fact retains manual provenance and does not prove airing. |
| D10 | **Deferred:** Leightronix formats, exchange and historical import contract | Samples will be examined when this roadmap work is authorized |
| D10a | Submission channels and delivery provenance | Dropbox, email, and SMB metadata only; no credentials, auto-fetch, network mounts, or V1 connectors |
| D11 | **Resolved deferred:** calendar export is out of the first release and planned as a near-term roadmap follow-up | Format and public fields can be decided later; private in-app day/week/agenda views remain V1 |
| D12 | **Resolved for V1:** manual local backup of the application folder or database | No automated backup, rotation, remote copy, or media purge. Whole data-folder backup is preferred; database-only may omit config/evidence and SQLite sidecars. Application backup retention/recovery details remain operational follow-up; media retention is deferred |
| D13 | **Confirmed:** existing LAN SMB storage is the permanent home, with exact lowercase layout `PUB-TV/<Show-Code>/source/` and `PUB-TV/<Show-Code>/encoded/`, no per-episode directories | SMB root value, show-code format, and filename convention remain open/deferred. Stable asset identity is handled separately by D08. Do not rename, move, mount, or reorganize files. |
| D14 | **Accepted:** after a premiere passes with a valid recorded matching uploaded schedule revision, classify its episode as previously scheduled and expose the next pending item for planning | This operational, idempotent transition is not confirmed airing and never assigns a future week, reorders/substitutes episodes, or consumes reruns. Cancelled or known failed premieres require owner review; future premieres and those with missing or invalid matching uploads stay pending; logs remain authoritative for actual airing. |

### Accepted contract/default: uploaded schedule revisions (D14a)

An `UploadedScheduleRevision` is a first-class upload record with an immutable internal ID and covered-revision set, optional operator-entered external label/reference, exactly one target device, operator/audit actor, upload timestamp, and the exact covered `Occurrence` revision IDs. Lifecycle is `active`, `superseded`, or `invalidated`. Supersession retains prior/newer links, reason, actor, and timestamp; record-level invalidation voids all coverage and retains reason, actor, and timestamp. A newer record explicitly lists every occurrence revision it certifies; it never inherits coverage from the prior record. Readiness and D14's queue transition require same-device, `active`, exact effective occurrence-revision coverage. Missing, stale, superseded, invalidated, or predecessor-linked coverage fails. Changes to an occurrence (episode/asset version, start, duration, status, target, reschedule, cancellation, or preemption) preserve the historical upload but make its prior occurrence-revision link stale. This upload contract remains distinct from preparation, transfer, and programming readiness and never implies airing.

Useful optional material for the next review: one representative full day, one show with premiere/replays and several episodes, and an example of a live event. Vendor schedule/as-run files can wait for the integration phase. No real source files have been supplied or ingested yet.

## Approval record

| Item | State |
| --- | --- |
| Planning documentation | Authorized by initial request |
| Product scope and scheduling semantics | Accepted for V1 implementation |
| Architecture, host and packaging approach | Python + Django 5.2 + SQLite accepted; unsigned PyInstaller `.app` build implemented for macOS 26.x/Apple Silicon; Intel requires a separate later build/validation |
| Source format / import contract | Explicitly deferred to roadmap |
| Executable scaffold and application implementation | V1 scaffold implemented; validation and pilot remain |

The owner approved the complete V1 scope and Python + Django + SQLite stack on 2026-09-09. The owner subsequently accepted macOS 26.x on Apple Silicon as the initial runtime, packaging, and testing target. Unsigned local PyInstaller arm64 packaging is implemented and smoke-tested. Signing, notarization, installation restrictions, distribution form, later Intel compatibility validation, application data path/backup operations, and SMB root/naming remain open or deferred as noted. Vendor exchange, device automation, public/LAN access, accounts, and calendar export remain deferred by scope.

## Change record

- 2026-09-10: Owner accepted and implementation added an at-a-glance upcoming reserved-show schedule using only show names and full reserved times. Episode-specific operational views retain virtual-channel filler.
- 2026-09-17: Owner accepted the complete usability overhaul: Today/unified Schedule/Shows/History/Settings IA, schedule-item workbench, Show workspace, contextual actions, compatibility redirects, derived status/timeline, canonical data ownership, migration-only legacy fields, and the explicit preparation/asset/device/occurrence/upload replay split. Implementation and pilot verification remain pending.
- 2026-09-10: Owner accepted and implementation added show descriptions, separate private producer phone and email fields, legacy contact preservation, conditional Other show-type detail, human-readable object labels, clearer AME/FTP/WinLGX preparation labels, hidden legacy preparation provenance, and the concise Queued episodes heading. The 2026-09-17 usability decision supersedes the former standalone upload-page disposition: record upload is contextual in the schedule-item workbench.
- 2026-09-09: Owner accepted stable unique Asset IDs and separate filenames for each source/encoded media asset, plus direct auditable reordering of the visible upcoming-premiere queue. The relationship between Episode Title and asset identification remains open; preparation-checklist redesign is deferred.
- 2026-09-09: Owner accepted D14a and the first-class `UploadedScheduleRevision` contract, including exact device/occurrence-revision matching, immutable coverage, supersession, whole-record invalidation, stale-link behavior, and queue/readiness consequences; upload never proves airing.
- Owner described the manual AME → FTP → WinLGX library → weekly schedule → UltraNexus upload workflow, the mono/audio/color preset intent, and inconsistent filenames/storage across encoding machines. D09 initially proposed six distinct stages; they are now accepted per D09. No technical preset parameters or storage changes have been approved.
- 2026-09-09: Owner accepted D08 minimum identification and optional descriptive/contact fields, unknown runtime, and D09's six separate manual facts with owner confirmation and live-entry N/A handling.
- 2026-09-09: Owner accepted D14's idempotent previously-scheduled queue transition after a passed premiere with a valid recorded matching upload; it is neither an airing claim nor a future assignment. Cancelled/known failed cases require review; future, missing, or invalid cases remain pending.
- 2026-09-09: Owner accepted the `America/Detroit` station-timezone mapping; private staff calendar scope retained.
- 2026-09-08: Initial documentation proposal; at that time no application source, dependencies, database, deployment, or device integration existed.
- 2026-09-08: Owner confirmed single-editor macOS use, Eastern/Monday 00:00 week, same-week replay rule with case-by-case old episodes, complete daily schedules, and deferred Leightronix exchange/public access.
- 2026-09-08: Owner confirmed fixed program starts, intentional labeled virtual-channel intervals, a 60-second ideal gap target, and over-slot episode flags; timestamp precision and live-overrun policy remain open.
- 2026-09-08: Owner confirmed producer-directed episode order (usually FIFO), intended premiere dates/order for unaired episodes, and Dropbox/email/SMB provenance separate from receipt and storage.
- 2026-09-08: Owner confirmed existing LAN SMB as the permanent home for source and encoded media; naming, root path, and media retention remain open/deferred. Application database/config backup is manual and local per resolved D12.
- 2026-09-08: Owner chose manual local V1 backups. Stop the app before copying the complete local data folder; database-only copies require clean shutdown/checkpoint and may omit required config/evidence or SQLite sidecars. No automated rotation or remote copy.
- 2026-09-09: Owner approved direct V1 app access without a separate login/password; the configured local owner is the audit actor. Loopback, host/origin, and CSRF protections remain required; LAN/public access and accounts remain deferred.
- 2026-09-09: Owner resolved seconds precision, fixed starts during live overruns, and over-slot flags. Ordinary recurrence is anchored to Eastern local wall-clock time year-round; DST transition-time policy remains open.
- 2026-09-09: Owner accepted macOS 26.x on Apple Silicon on the development machine as the initial runtime, packaging, and testing target. Intel Mac compatibility is deferred for later validation; no universal binary is promised. Packaging mechanics and installation restrictions remain open.
- 2026-09-08: Owner confirmed exact lowercase shared layout with all source files under `source/` and encoded files under `encoded/` per show, with no per-episode folders; all further naming and ID questions remain deferred.

- 2026-09-09: Owner confirmed Sunday 01:00–03:00 is curated supplemental filler without producer-assigned slots and explicitly directed no further effort on daylight-saving edge cases. Closed as a planning issue.
- 2026-09-09: Owner accepted calendar capacity semantics: Available is outside planned occurrences and active recurring reservations; virtual-channel filler remains reserved, with missing runtime shown as an undetermined filler amount. Owner accepted the controlled Show type vocabulary and Day/Week-only Reserved/Available filters with accessible text legends.
