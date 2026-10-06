# ChannelDex scheduling and media-tracking plan

## Usability overhaul — accepted for implementation (2026-09-17)

The owner accepted the complete usability and information-architecture overhaul
identified in the 2026-09-17 review. This is an implementation contract, not a
claim that the runtime already provides these workflows. The target owner-facing
navigation is **Today**, **Schedule** (Day, Week, Agenda, Recurring Times),
**Shows**, **History**, and **Settings**. Contextual actions replace duplicate
global commands; compatibility redirects preserve existing bookmarked routes.

Today is the operational start page: selected-date timeline, readiness, warnings,
and remaining work. Schedule views share one date/filter/action surface. A
schedule-item workbench combines plan, episode/media, preparation, device
programming, uploaded-schedule coverage, airing evidence, and activity without
merging their underlying records. A Show workspace combines show metadata,
primary producer, episodes/queue, weekly times, premiere selection, and upcoming
occurrences. History is principally read-only; current work starts from the
affected item and returns to that context after save.

The accepted simplifications include one derived status presentation, one
derived preparation/activity timeline, inline creation of supporting records,
progressive/contextual forms, human-readable labels, and removal of duplicate
owner-facing fields. Legacy values remain migration-only metadata and are not
shown as parallel editable facts.

The preparation/replay question is resolved: source availability and encoding
belong to the exact reusable asset version; transfer belongs to asset version +
target device; library registration is part of that asset/device handoff;
playback programming belongs to occurrence + target device; and uploaded
schedule coverage belongs to an immutable upload and its exact occurrence
revision IDs. Replays reuse the prepared asset facts, while occurrence-specific
programming and upload coverage are never copied silently. Show-level slot
length is the current setting; each occurrence snapshots its planned length.
Episode schedule labels are derived; free-text labels remain for standalone IDs,
PSAs, filler, and live entries.

The following distinctions remain accepted and must not be merged: plan versus
aired evidence; recurring reservation versus concrete occurrence; delivery
notice versus actual receipt; source versus encoded rendition; fixed slot versus
media runtime; preparation versus programming versus uploaded-schedule coverage;
premiere queue classification versus media readiness; and reported observation
versus log-verified evidence. The UI presents these together where useful but
keeps their provenance and state independent.

## Purpose and scope

ChannelDex needs a self-contained browser application on one computer to coordinate recurring programming, playlist preparation notes, media management notes, and manual air history. It is designed for PUB-TV first and may later support GOV-TV. Its first release is an operational record-keeping tool, not a playout controller or transcoding system.

### User-confirmed requirements

- Track recurring weekly shows, producer-supplied descriptions, private producer phone and email, upcoming episode slates, prior confirmed air dates, and a complete 24-hour station schedule.
- Represent a weekly episode's premiere and its replays.
- Replays always use the episode selected for that premiere cycle. By default, the owner selects any older episode case by case. The owner may instead enable a global, off-by-default standing authorization that carries the most recent explicitly planned episode into otherwise unassigned current/future weeks. Generated cycles are visibly labeled and audited; an explicit episode or No program plan overrides them, No program stops subsequent carry-forward, disabling the setting stops future generation without erasing existing plans, and the behavior never advances the pending-new queue.
- Episode backlog order follows producer direction, usually FIFO. Unaired episodes carry intended air order and optional intended premiere dates; producer-directed ordering wins. Date, order, and fixed-slot conflicts are flagged, never silently rescheduled or advanced. Reruns leave the pending-new queue intact.
- Record submission channel separately from receipt and storage: Dropbox folder/link, email sender/message/download URL, or local SMB path. Link arrival is not receipt; no credentials, automatic fetching, network mounts, or V1 connectors are assumed.
- Track six separate manual preparation facts: source available; AME preset/version/machine/output; exact FTP output/device; WinLGX library registration; slot assignment; and uploaded schedule revision. These retain owner confirmation and provenance; transfer is per exact asset version/target and slot assignment per occurrence/target.
- Schedule episodes/media, station IDs, PSAs, filler, and live events. Live events can explicitly have no file/transcode requirement.
- Provide private in-app day/week/agenda calendar views and an at-a-glance upcoming reserved-show schedule that displays only show names and reserved times. Keep producer contacts private. Public access is deferred; calendar export is out of the first release and a near-term roadmap follow-up.
- Keep current operations compatible with Leightronix Ultra-Nexus HD. A possible TelVue replacement in roughly six months is undecided.
- Plan for future vendor-file ingestion only after PUB-TV supplies samples. Do not assume formats or APIs.
- Preserve evidence of actual airings; a schedule is intent, not proof that a program ran.
- Airing verification is occasional: TV sightings or producer reports are attributed reports, while UltraNexus logs are the authoritative source when a show is questioned. The owner may manually record a log reference/date; no automatic confirmation or import is required, and planning is never blocked by missing logs.
- After a premiere passes, if its matching uploaded schedule revision was recorded, the episode becomes previously scheduled and the next queue item becomes available for planning. This idempotent queue transition is not an airing claim, does not auto-assign a future week, and does not consume reruns; cancelled or known failed premieres require owner review. Future premieres and those lacking a valid matching upload remain pending.
- Program starts are fixed to planned slots. Between programs, the station returns to its virtual channel for schedule, PSA slides, and rotating graphics. A reservation's known remainder after shorter content is labeled virtual-channel filler; when runtime is missing, only the filler amount is not determined. A 60-second gap is an ideal soft target, and episodes exceeding their slot are flagged without automatic shifting, trimming, filler, or playout control.

### Accepted initial platform target

The initial runtime, packaging, and testing target is macOS 26.x on Apple Silicon on the development machine. Unsigned local PyInstaller arm64 packaging is implemented and smoke-tested. Intel Mac support is a later compatibility-validation target and is not yet proven; the product does not promise one universal binary. Signing, notarization, installation restrictions, and the final distribution form remain open/deferred.

### Proposed MVP defaults

One owner edits authoritative data on a macOS computer and manually backs up the local application data folder or database. Private in-app day/week/agenda views are sufficient for V1. Existing storage has been the historical media location; the approved future home for both source and encoded media is the LAN SMB store. The app stores searchable metadata and references, not media bytes.

The accepted post-MVP UltraNEXUS extension supports qualified local media
encoding or validated bypass plus verified FTP transfer after Approval 1.
The 2026-10-01 restricted extension adds local paired NMG/BIN generation and
an attended delivery workflow after separate Approval 2. Hardware mutation
remains gated by target-specific BIN, activation, and recovery qualification;
full-week replacement is deferred.
Live monitoring and automatic airing evidence remain outside this capability.

Propose a first-class `UploadedScheduleRevision` for each manually uploaded external schedule: immutable internal identity and covered occurrence revision IDs; optional operator-entered external label/reference; exactly one target device; operator/audit actor; upload timestamp; and lifecycle state (`active`, `superseded`, or `invalidated`). Supersession retains prior/newer links, reason, actor, and timestamp. Record-level invalidation voids all coverage and retains reason, actor, and timestamp.

## Reported media preparation context

The owner uses a saved Adobe Media Encoder preset based on Leightronix's documented audio/video requirements. The owner reports that the station currently carries mono and drops the right stereo signal; the preset downmixes both channels to mono to retain right-channel content. It also applies audio compression/limiting/loudness correction and checks/adjusts legal broadcast colors. These describe the existing workflow, not independently verified preset settings or guarantees; exact codec, channel mapping, levels, and color parameters have not been supplied.

The confirmed shared layout is `PUB-TV/<Show-Code>/source/` for all source episode files and `PUB-TV/<Show-Code>/encoded/` for all encoded episode files, using exact lowercase folder names and no per-episode directories. `generic existing filename.ext` illustrates a file reference only; it does not prescribe a codec or container. Track source and derived encoded files independently even when names differ. The SMB root, show-code/ID format, and all further naming questions remain deferred.

## Target screens (accepted for implementation)

Calendar is primary: full-day/week grid plus agenda, with item type, premiere/replay label, and visible gap/overlap warnings. Week offers two non-redundant modes: a continuous Available/Reserved capacity map, or individual scheduled items plus consecutive Available blocks. It does not repeat a Reserved capacity block behind the scheduled item representing that reservation. A separate recurring-times view expands reservations at their full configured length and shows only show names and times, without episode names, Available intervals, preparation state, or virtual-channel filler rows. Show detail holds the producer-supplied description, private producer phone and email, this week's selected episode, and a distinct next-on-slate field. The episode library shows prior actual air dates. The schedule-item workbench shows preparation, programming, and upload coverage using the canonical ownership split above; legacy provenance remains preserved but hidden from owner-facing forms. A chronological day log shows item, time, planned duration, readiness, and actual evidence. “Next scheduled” is derived from occurrences; “next on slate” is editorial planning and is never treated as the same value.

## Primary workflows

1. The owner fills the day with scheduled items: program episodes/media, station IDs, PSAs, filler, or live events. Fixed starts remain fixed; the calendar labels reserved time, available time, and virtual-channel filler, offers a proposed advisory for gaps under 60 seconds, and never auto-fills gaps or drives playout.
2. For a recurring show, ChannelDex suggests the next queued episode and next configured premiere. The owner confirms that premiere cycle or explicitly chooses an older episode/no-program case. Replays after the premiere use that episode until the next premiere; suggestions never become plans without owner confirmation.
3. The owner records delivery metadata and planned queue order. A delivery may group one or many episodes/assets and keeps notification and received timestamps distinct; storage destination is separate from submission source.
4. The configured owner records six distinct manual checklist facts: source available (download or already in SMB, not merely a received link); AME preset/version/machine/output; exact FTP output/device; WinLGX library registration; slot assignment; and uploaded schedule revision. File-related facts may be N/A for a live entry. Preparation, transfer, programming, and upload remain distinct and device-specific; upload does not prove airing. The upload record must explicitly list every occurrence revision it is intended to certify.
5. The owner records version metadata and preparation/log entries. Source and encoded outputs are distinct related renditions; an exact version/time/library mapping or schedule revision change invalidates only dependent confirmations while retaining history.
6. If an item is cancelled, preempted, or rescheduled, the owner records the reason. Airing evidence records an actual end for a live event when known.
7. After an airing, the owner may record scheduled past/unverified, reported/observed (with source), or log-verified actual evidence. Discrepancies and corrections remain visible; unresolved matches stay unresolved. Planned schedules remain separate from confirmed air history.

## Rules and edge cases

- A recurring slot generates intended occurrences only during its effective interval. Slot changes do not rewrite past occurrences. Every occurrence has a planned duration; an asset/episode is optional for a live event.
- A slot's start is fixed. Distinguish fixed slot end from selected asset runtime and expected content end. A shorter known runtime produces a reserved virtual-channel filler interval; missing runtime leaves only its filler amount not determined, never available. Episodes exceeding their slot are flagged; no automatic schedule adjustment is applied.
- Calendar weeks begin Monday 00:00 Eastern, but episode reuse follows premiere-to-premiere cycles. A cycle has one owner-confirmed episode: a pending episode is a new premiere, while a previously scheduled episode is an explicit older rerun. Replays after the premiere use it until the next premiere; a Monday replay before a Wednesday premiere retains the prior cycle's episode. A Sunday item running past midnight remains one Sunday-start occurrence.
- A concrete occurrence is planned, cancelled, preempted, or rescheduled. These are schedule states only; none implies that it aired. Cancellation/preemption retains the original plan and reason.
- Upload readiness is exact, not inferred: the active uploaded revision must target the same device and explicitly cover the exact effective occurrence revision. A missing, stale, superseded, invalidated, or predecessor-linked record fails. Changes to episode/asset version, start, duration, status, target, reschedule, cancellation, or preemption preserve the historical upload but make its old occurrence-revision link stale; a later active upload must explicitly cover the replacement revision.
- Proposed `America/Detroit` is the station-timezone mapping for confirmed Eastern Time. Recurrence is anchored to the Eastern local wall clock, so a 5:00 show remains 5:00 year-round rather than drifting by an hour with UTC. It is independent of the macOS system timezone. Durations spanning midnight are valid. A cross-midnight item has one identity and is sliced only for each day's display. DST days account for the whole local day (23 or 25 elapsed hours). Sunday 01:00–03:00 is curated supplemental filler with no assigned producer slots. The owner closed transition handling as a planning issue; it needs no further owner decision or dedicated planning work.
- An airing record is independent from the occurrence it may match. It retains source evidence and timestamps; corrections create a revision linked to the prior record rather than erasing it.
- Prior confirmed dates derive from authoritative log-backed records; reports are never silently upgraded. Keep last scheduled/intended premiere separate from last log-verified date. A log may confirm different content or a failed/skipped run.
- Changes to schedules, assignments, preparation/logs, and airing records retain date/time history for the sole owner.

## Open questions before rollout

- V1 backup is manual and local; document the chosen data-folder location and safe copy/restore steps before rollout. Media retention remains deferred. Calendar export format and public fields are a near-term roadmap decision.
- Which Leightronix exports are available, which fields are trustworthy as as-run evidence, and what device identifiers should be tracked? This is future roadmap discovery; TelVue remains an option only after product and export/API details are confirmed.
- The saved AME preset defines the current deliverable. Its exact technical settings and identifying label/version can be documented later; v1 records the preset used without automating or certifying processing.

## Acceptance scenarios

| Scenario | Expected result |
| --- | --- |
| Wednesday premiere with Monday replay | Monday before the premiere uses the prior cycle's episode; Wednesday starts the new owner-confirmed cycle, whose episode continues through following replays until the next premiere. |
| Asset received for two devices | Receipt is shared asset metadata; transfer/readiness can be complete for Ultra-Nexus and incomplete for another device without conflict. |
| Unknown runtime | An episode may be identified and planned with runtime unknown; its resulting interval remains unknown rather than being treated as zero. |
| Live-entry checklist | A live entry can record file-related checklist facts as N/A while retaining its separately recorded slot assignment and uploaded schedule revision. |
| Air history corrected | The owner revises the evidence-backed manual airing record while retaining the original assertion/history; the scheduled occurrence remains distinct. |
| Full-day mix | Episodes, IDs, PSAs, filler, and a live event can fill a local day. Any remaining gap or overlap is visible; nothing is added automatically. |
| Fixed slot and virtual channel | A 7:00–7:30 slot with a 27:42 asset leaves 2:18 intended virtual channel; 29:30 leaves a proposed 0:30 advisory; 30:10 exceeds the slot and is flagged. |
| Delivery provenance and queue | A Dropbox batch groups two episodes with notification and received timestamps; a producer-directed order overrides proposed receipt FIFO, and an expired link is noted without live checking or implying receipt. |
| Asset replacement or schedule edit | A replacement version needs new preparation/transfer/programming. An episode/time/target edit invalidates dependent programming only, preserving prior log history and unaffected transfer evidence. |
| Stale uploaded coverage | An uploaded revision covers occurrence revision R1, then the occurrence is rescheduled to R2. R1 coverage is invalid for readiness and the premiere queue transition; R2 remains pending until an active upload explicitly covers R2. |
| Superseded uploaded revision | Revision U1 covers R1 and is superseded by U2. U1 is non-current and fails readiness even if its device and covered IDs match; U2 certifies R1 only if U2 explicitly lists R1. |
| Passed premiere queue | A passed premiere with a valid matching uploaded revision is classified previously scheduled once and exposes the next pending item for planning. It does not assert airing or assign a future week; cancelled/known failed, future, missing, or invalid cases remain pending or require review as applicable. |
| Midnight/DST overrun | One cross-midnight item renders in both day views. Evidence may record a live item's actual end; a DST day accounts for its 23/25 elapsed hours and exposes gaps/overlaps. |
## Accepted library-first intake (2026-10-05)

Media Preparation is the primary intake workspace. The owner chooses one show and one destination, adds multiple local episode paths through the browser's native chooser or an editable absolute path, and may append or remove rows before review. Drag-and-drop and native new-window workflows are outside the accepted interaction. Each row records an existing episode or a proposed title, optional episode number, measured or manually entered runtime with provenance, and a required encoding choice. Apply-all is a convenience only; each row remains reviewable.

One concise review then adds episodes and source/encoded assets to the Library and queues preparation. Intake has no premiere choice. Planning remains a separate action and may use media that is still pending. Existing reviews and guided delivery records remain readable and compatible.

Library readiness, source availability, runtime, media versions, preparation, and transfer are separate facts. Relinking starts a durable background review. A matching fingerprint can update the path after explicit confirmation; changed or unknown content creates a new reviewed intake/version route. Active work is protected, queued work requires refreshed review, and historical evidence is never rewritten. These are accepted product contracts; production qualification and external tool behavior remain open.
