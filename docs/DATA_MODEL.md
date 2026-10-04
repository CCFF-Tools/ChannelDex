# ChannelDex conceptual data model

## Accepted usability contract (2026-09-17)

The model is presented through five owner workflows: Today, unified Schedule,
Shows, History, and Settings. These are views over the entities below, not new
storage boundaries. A schedule-item workbench is the canonical contextual view
for plan, media, preparation, programming, upload coverage, airing evidence, and
activity. A Show workspace is the canonical view for show metadata, producer,
episodes/queue, recurring times, and occurrences. Compatibility redirects may
keep legacy URLs usable during migration.

Canonical ownership for the redesign is: Show -> primary Producer, with an
optional Episode producer override; filename is a property of each MediaAsset,
not a duplicate label; delivery notification and actual file receipt remain
distinct facts; the current Show-level slot length is inherited by future
occurrences, whose planned-length snapshots preserve history; asset preparation
is per exact asset version; transfer/library registration is per asset version +
Device; playback programming is per Occurrence + Device; and an
UploadedScheduleRevision explicitly covers exact occurrence revisions. Schedule
labels for episode items are derived from show/episode context. Legacy duplicate
fields (free-text producer/contact values, mirrored asset labels, redundant
received/workflow entries, preparation provenance, free-text upload revision,
copied durations, and duplicate schedule labels) are migration-only metadata,
not user-facing editable facts.

The accepted replay rule is that reusable asset preparation carries to every
replay of the same exact asset version. Occurrence-specific programming,
rescheduling, target changes, and upload coverage remain independent and must be
re-recorded when their occurrence revision changes. No derived status or timeline
may imply airing.

The following are deliberately separate model distinctions: plan vs aired
evidence; reservation vs occurrence; notice vs receipt; source vs encoded;
fixed slot vs runtime; preparation vs programming vs upload; queue classification
vs readiness; and reported vs log-verified evidence.

This is a conceptual model for the single-owner MVP, not a database migration or an assumed device/API contract. Every business identifier is scoped to a station unless explicitly global. PUB-TV is the initial station; GOV-TV can be added later without merging its schedules, contacts, or operational history.

## Core entities

### UltraNEXUS automation records

`UltraNexusTargetSettings` versions connection and qualification settings while
storing only a Keychain reference. `PreparationBatch` and `PreparationJob` retain
Approval 1 and its execution. `MediaInspection`, `MediaBinding`, `MediaIdAllocation`, and
`TransferAttempt` preserve exact bytes, probe evidence, controller identifiers,
permanent target-scoped ID reservations, and remote verification. Historical
bindings are backfilled into the allocation ledger during migration; prior
qualified base versions also reserve their imported IDs. New reservations are
created transactionally before transfer, survive failed work, and are reused
only for the same exact media hash and case-folded filename.
`ControllerSnapshot`, `SchedulePublicationBatch`,
`OccurrenceRevisionSelection`, `ArtifactRevision`, `PublicationJob`, and
`ActivationEvidence` keep schedule review, Approval 2, delivery, and controller
acceptance distinct. `ResearchGate` links each disabled capability to external
evidence. These facts never create `AiringEvidence` automatically.

The restricted extension adds versioned exact-hash NMG/BIN bases, controller
family, firmware, output, media-profile, template/profile identity hashes,
qualification status/evidence, and a separate opaque XPASS Keychain reference
to target settings. Each occurrence selection
records add, move, replace, or delete plus the source BIN slot and record hash
when editing an existing event. `ScheduleDeliveryOperation` retains the exact
candidate, approval hash, known-good local/remote rollback identifiers, and
durable states through staging, promotion, acknowledgement, observation, and
rollback. An ambiguous operation blocks another delivery on that target until
review. Artifact manifests retain exact shared-plan byte ranges, the paired NMG/BIN parity report,
and introduced resource references so registration qualification is enforced
only when needed. Coverage is recorded only after independent activation
observation.

| Entity | Responsibility and key relationships |
| --- | --- |
| **Station** | Internal identity, confirmed Eastern Time, Monday 00:00 Eastern week start, proposed `America/Detroit` station-timezone mapping, and the off-by-default global episode carry-forward setting; owns shows, targets, calendars, and station-scoped identifiers. This is independent of the macOS system timezone. |
| **Show** | Internal identity and station relationship for a recurring station series, with title, producer-supplied description, optional private producer phone and email, usual submission method, slots, episodes, and owner metadata. Unclassified historical contact text is preserved separately for owner review. Station-level code/title uniqueness is proposed, pending policy. |
| **Producer** | Internal immutable identity, first and last name, optional private phone, email, notes, station boundary, and optional external membership number reserved for possible future association. Unclassified historical contact text is preserved separately for owner review. The owner never supplies or sees an application ID as the producer's name. No MyTurn lookup or integration is implied. |
| **Episode** | Internal identity and show relationship for a show installment, optional Producer relationship, title, runtime, received time, planned episode order, and optional planned premiere date. The internal `status` field remains the distinct pending/previously-scheduled queue classification; it is not the manual workflow stage or airing evidence. The retired free-text producer-ID value is preserved only as non-owner-facing legacy metadata during migration. |
| **EpisodeWorkflowMilestone** | Append-only, progressive manual facts `Received`, `Downloaded`, `Encoded`, `Transferred`, and `Scheduled`, each with completion time, actor, provenance, and notes. The latest completed stage is a concise display value; earlier facts remain intact. `Scheduled` never proves airing. |
| **MediaAsset** | Reusable source or encoded rendition with optional episode link, a stable unique Asset ID, and a separately recorded filename; each has a `FileReference` role/location/machine and related rendition links. Source and encoded files are distinct assets and may have different names. Media bytes reside in the confirmed SMB home; the app stores references. The episode Title remains separate pending the open owner-facing identification decision. |
| **Submission/Delivery** | Private provenance for one or many episodes/assets: actual method (may differ from the show default), grouped batch, notification and received timestamps, location/reference, and optional availability notes. Methods include Dropbox, email, and SMB; source metadata is separate from storage destination and does not imply receipt. |
| **Device** | A station-owned operational target (initially Ultra-Nexus HD; later TelVue or another target). Device type/capabilities are descriptive until supported by evidence. |
| **AssetPreparation** | Per asset-version receipt/transcode checklist/notes and dates. It is independent of a target and does not imply scheduled use. |
| **AssetTargetTransfer** | Per exact asset version and target transfer/validation state, reference, and date. A later asset-version or target change does not retain this confirmation. |
| **MediaChecklistEvidence** | Legacy compatibility projection of the six manual facts. New owner-facing workflows derive source/encoding from asset version, transfer/library registration from asset version + device, programming from occurrence + device, and upload coverage from `UploadedScheduleRevision`. File-related facts may be N/A for live entries. Existing provenance remains stored as migration-only audit metadata and is hidden from owner-facing forms. These facts do not imply automated validation or airing. |
| **RecurrenceSlot** | Effective-dated weekly intent: station, optional show, zero-based Monday–Sunday weekday stored internally but displayed by name, Eastern local time, second-precision duration presented as hours/minutes, and premiere/replay role. Exactly one active show slot is the premiere slot; other active slots replay that week's explicit selection. Used definitions are superseded with a new effective-dated version rather than rewriting prior occurrences. |
| **WeeklyEpisodeAssignment** | Legacy internal name for a premiere cycle: actual premiere date, derived Monday calendar bucket, queue-suggested new episode or explicitly chosen older episode (or no-program), and the replays from that premiere until the next premiere. A provenance flag distinguishes a visible, audited carry-forward cycle created under the station-wide standing authorization from an explicit owner plan. The calendar week remains Monday-based, but episode reuse does not reset on Monday. |
| **Occurrence** | A concrete scheduled item, generated or manual: type `episode/media`, `station_id`, `PSA`, `filler`, or `live`; fixed start, slot end, selected asset version/runtime, expected content end, optional reserved virtual-channel filler interval, status planned/cancelled/preempted/rescheduled, and reason. Generated occurrences snapshot their weekly role (`New premiere`, `Replay`, or explicit older rerun) so later slot-definition changes do not relabel history. Live items can declare media preparation N/A. Missing runtime leaves filler amount not determined, never available. |
| **OccurrenceProgramming** | Per occurrence and target programmed confirmation/note. For media it pins the exact asset version; for live it pins the live revision, start, planned duration, and target. It is separate from asset preparation and transfer. |
| **UploadedScheduleRevision** *(proposed)* | A first-class record of one manually uploaded external schedule revision: immutable internal ID; optional operator-entered external label/reference; exactly one target `Device`; uploading operator/audit actor; upload timestamp; the immutable exact set of covered `Occurrence` revision IDs; and lifecycle state `active`, `superseded`, or `invalidated`. Supersession retains links to the prior and newer upload records, reason, actor, and timestamp. Record-level invalidation voids all of the upload's coverage and retains reason, actor, and timestamp. This record is provenance for schedule upload, not evidence that anything aired. |
| **AiringRecord** | Evidence-based airing claim with state reported/observed or log-verified actual; scheduled-past/unverified is derived from occurrences without inventing an evidence record; actual times, station/device, identifiable episode/asset, optional occurrence match, confidence, and source. Corrections supersede; unresolved matches remain unresolved. |
| **ManualEvidence** | Owner-entered source assertion, actor/date, observed details or UltraNexus log reference/check date, and provenance. Reports do not become log-verified without an explicit log check. |
| **OperationalLogEntry** / **ChangeHistory** | Configured-owner actor, time, preparation/log action or historical change, entity/version, status/note, and before/after or change summary. |

## Relationship and history rules

`Station -> Show -> Episode -> MediaAsset(version)` captures content lineage; standalone assets have no episode. `Show -> RecurrenceSlot` captures recurring intent and one shared show-level slot length. `WeeklyEpisodeAssignment -> Occurrence` captures an explicit owner plan or a labeled, audited cycle generated under the station-wide carry-forward authorization; generated occurrences snapshot their premiere/replay context. The calendar still groups Monday through Sunday, and an occurrence crossing midnight remains one occurrence. `MediaAsset -> AssetPreparation -> AssetTargetTransfer` separates receipt/transcode from transfer. `Occurrence -> OccurrenceProgramming` pins media to asset version/target and live to revision/start/duration/target. `UploadedScheduleRevision -> Occurrence(revision)` records exact upload coverage for one device. `AiringRecord -> ManualEvidence` is evidence, never schedule proof.

Occurrence has an immutable ID and versioned planned revisions. Generated occurrences use a stable station-scoped token from recurrence-slot lineage plus original nominal local date/time, never mutable episode/time. Re-generation is a no-op or previews a revision; it cannot resurrect cancelled/rescheduled items. A reschedule retains the original and links replacement via `replaces`/`superseded_by`; only one effective plan counts for coverage. Manual entries use UUIDs. Slot edits preview future replacements and never rewrite history.

Replacement asset versions require new preparation, transfer, and programming. An episode, start/time, or target change invalidates dependent programming, not an unchanged asset-version/target transfer. Earlier confirmations remain in history; airing corrections retain the original assertion/provenance.

Encoding creates a derived rendition linked to its source; it never overwrites the source. A new encoded rendition invalidates transfer, library registration, and schedule-upload confirmations for that output while source-available evidence remains valid. Record the preset label/version actually used; preset edits do not retroactively certify existing outputs.

An uploaded schedule revision is immutable in identity and covered-revision set. Its lifecycle may move from `active` to `superseded` (with explicit prior/newer links, reason, actor, and timestamp) or `invalidated` (voiding the whole upload with explicit reason, actor, and timestamp); records remain queryable after either transition. A newer upload does not inherit coverage: it must explicitly list every occurrence revision it is intended to certify.

## State and visibility

Schedule states are planned, cancelled, preempted, and rescheduled; none asserts an airing. Assignment distinguishes pending/no program, selected new premiere, and selected older rerun. Receipt/transcode is per asset version, transfer per asset version/target, and programming per occurrence/version/target.

Time passing, upload, or matching a guessed time is never automatic actual-air confirmation. A log-backed record may show different content or a failed/skipped run. Keep scheduled/intended premiere, reported observation, and last log-verified date separate.

Preparation, transfer, programming, and upload readiness are separate, device-specific facts. For an occurrence, an uploaded schedule revision is an exact match only when it targets the same device, is `active`, and explicitly covers the exact effective occurrence revision ID. A stale, superseded, invalidated, missing, or predecessor-linked revision fails; matching an occurrence identity, date, or guessed schedule does not substitute for its revision ID. If an occurrence changes episode/asset version, start, duration, status, target, or is rescheduled/cancelled/preempted, the historical upload record and link remain intact, but coverage of the prior occurrence revision becomes stale for readiness and the queue transition. The new effective revision must be uploaded explicitly.

Queue order is producer-directed, usually FIFO; recorded-receipt FIFO is only a proposed initial suggestion. The owner may persistently reorder the visible pending queue by drag-and-drop or an accessible equivalent, and each saved reorder is audited. Intended premiere dates map to existing fixed premiere slots and conflicts are flagged. Reruns do not remove an episode from the pending-new queue. Submission URLs may expire; availability notes are optional and are not live checks. Different encode machines may record mount-specific paths alongside a stable SMB-relative location. No credentials, connector fetch, download automation, mount automation, or storage reorganization is modeled for V1.

When a premiere has passed and an `active` uploaded schedule revision for the matching device explicitly covers the exact effective premiere occurrence revision, an idempotent operational transition marks the episode previously scheduled and exposes the next queue item for planning. It never asserts airing, auto-assigns a future week, reorders/substitutes episodes, or consumes replay assignments. Cancelled or known failed premieres require owner review. Future premieres and those lacking an exact match remain pending.

The confirmed media layout is `PUB-TV/<Show-Code>/source/` and `PUB-TV/<Show-Code>/encoded/`, exact lowercase, with all files for a show in each folder and no per-episode directories. Every media asset has an application-generated stable UUID and a separate filename; no filename-derived ordering or identity is assumed. The filename convention and show-code format remain open.

Recurrence uses Eastern local wall-clock rules, never fixed UTC recurrence; a 5:00 show remains 5:00 year-round. Sunday 01:00–03:00 is curated supplemental filler with no assigned producer slots; the owner closed transition handling as a planning issue.

Calendar derivation treats every local day as full coverage. It displays gaps and overlaps and does not auto-create filler. Cross-midnight occurrences retain one identity and are sliced for day views. Planned duration is always recorded; actual end is evidence on an airing record. DST day calculations honor the full local date and its 23/25 elapsed-hour duration, with explicit handling for ambiguous local times. The accepted Eastern local-wall-clock rule is represented by proposed `America/Detroit`; no further DST-transition policy is reopened here.

Fixed starts remain fixed. A shorter known runtime may produce a labeled virtual-channel filler interval inside reserved time; missing runtime leaves only the filler amount not determined. Available means outside both planned occurrences and active recurring reservations. Under-60-second gaps may receive a proposed advisory, while over-slot episodes are flagged. These are planned coverage calculations, never proof that content aired, and the app does not assert or manage rotator contents.

Calendar show-type filters apply only to planned show-item cards. Capacity remains station-wide so filtering cannot turn other reservations into Available time.

The MVP is private to its owner. Producer contacts and operational notes remain private. Private in-app day/week/agenda views are V1; public access is deferred and calendar export is a near-term roadmap follow-up. Media retention scheduling is deferred; no automatic expiry or deletion is implied.

## Deliberately deferred design choices

The model still requires detailed application backup/recovery procedure; V1 backup is manual and local. Calendar export format/public fields are deferred roadmap decisions. Media retention scheduling is deferred. Multi-user roles, LAN access, and device integrations are outside the MVP. A future **ImportBatch/ImportRow** extension may be designed only from representative Leightronix/TelVue samples; no export shape, API, or live-control capability is implied.

## Media queue and publication cycle provenance

**Accepted additive contract:** PreparationBatch gains nullable show and a unique
nullable submission UUID for idempotent intake. Existing batches remain valid.
Each PreparationBatchItem retains its asset/episode identity, file-row position,
optional historical occurrence link, and granular durable execution state.

PublicationCycleSelection records publication, confirmed assignment, exact asset,
optional originating preparation item, and reviewed snapshot. A cycle may be
reviewed in multiple revisions; a prepared asset may serve several cycles. The
legacy SchedulePublicationBatch.preparation_item link remains for historical
records; new cycle publication does not use it to constrain media reuse. A unique
nullable review token prevents duplicate confirmation from creating publications.
No migrations delete or rewrite existing evidence.
## Accepted 2026-10-04 fresh controller BIN contract

`Station.auto_pull_controller_schedule` defaults true and changes are audited. Each publication binds an immutable `ControllerSnapshot` containing the live `/internal/schedule/schedule.bin` bytes, SHA-256, trigger, actor, settings revision/hash, qualification context, target path, and capture time. Manual pulls are always available; identical pulls remain separate audit records. `SchedulePublicationBatch` stores the bound snapshot hash, mutation-plan hash, review diff, and generation kind. BIN and NMG `ArtifactRevision` records are labeled `scope=change_set` and carry the captured controller hash; legacy static base paths/hashes remain qualification/history only.
