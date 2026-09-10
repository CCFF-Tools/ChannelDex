# PUB-TV scheduling and media-tracking plan

## Purpose and scope

PUB-TV needs a self-contained browser application on one computer to coordinate recurring programming, playlist preparation notes, media management notes, and manual air history. It is designed for PUB-TV first and may later support GOV-TV. Its first release is an operational record-keeping tool, not a playout controller or transcoding system.

### User-confirmed requirements

- Track recurring weekly shows, producer-supplied descriptions, private producer phone and email, upcoming episode slates, prior confirmed air dates, and a complete 24-hour station schedule.
- Represent a weekly episode's premiere and its replays.
- Replays always use the episode selected for that week's premiere. When there is no new premiere, the owner selects any older episode case by case; the app must not automatically cycle or silently reuse one.
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

The MVP supports manual external media and device work: no app-controlled device exchange/import, schedule push, live monitoring, or transcoding. Leightronix/TelVue integration is future roadmap work.

Propose a first-class `UploadedScheduleRevision` for each manually uploaded external schedule: immutable internal identity and covered occurrence revision IDs; optional operator-entered external label/reference; exactly one target device; operator/audit actor; upload timestamp; and lifecycle state (`active`, `superseded`, or `invalidated`). Supersession retains prior/newer links, reason, actor, and timestamp. Record-level invalidation voids all coverage and retains reason, actor, and timestamp.

## Reported media preparation context

The owner uses a saved Adobe Media Encoder preset based on Leightronix's documented audio/video requirements. The owner reports that the station currently carries mono and drops the right stereo signal; the preset downmixes both channels to mono to retain right-channel content. It also applies audio compression/limiting/loudness correction and checks/adjusts legal broadcast colors. These describe the existing workflow, not independently verified preset settings or guarantees; exact codec, channel mapping, levels, and color parameters have not been supplied.

The confirmed shared layout is `PUB-TV/<Show-Code>/source/` for all source episode files and `PUB-TV/<Show-Code>/encoded/` for all encoded episode files, using exact lowercase folder names and no per-episode directories. `generic existing filename.ext` illustrates a file reference only; it does not prescribe a codec or container. Track source and derived encoded files independently even when names differ. The SMB root, show-code/ID format, and all further naming questions remain deferred.

## Proposed screens

Calendar is primary: full-day/week grid plus agenda, with item type, premiere/replay label, and visible gap/overlap warnings. A separate upcoming-shows view expands recurring reservations at their full configured length and shows only show names and times, without episode names, Available intervals, preparation state, or virtual-channel filler rows. Show detail holds the producer-supplied description, private producer phone and email, this week's selected episode, and a distinct next-on-slate field. The episode library shows prior actual air dates. A preparation queue displays the six separate preparation facts: source availability, Adobe Media Encoder preset/version/machine/output, exact FTP output/device, WinLGX library registration, WinLGX playback slot, and uploaded schedule revision, with notes and N/A where applicable. Existing preparation provenance remains preserved but hidden from the owner-facing form while preparation fact ownership and replay reuse are resolved. A chronological day log shows item, time, planned duration, readiness, and actual evidence. “Next scheduled” is derived from occurrences; “next on slate” is editorial planning and is never treated as the same value.

## Primary workflows

1. The owner fills the day with scheduled items: program episodes/media, station IDs, PSAs, filler, or live events. Fixed starts remain fixed; the calendar labels reserved time, available time, and virtual-channel filler, offers a proposed advisory for gaps under 60 seconds, and never auto-fills gaps or drives playout.
2. For a recurring show, PUB-TV suggests the next queued episode and next configured premiere. The owner confirms that premiere cycle or explicitly chooses an older episode/no-program case. Replays after the premiere use that episode until the next premiere; suggestions never become plans without owner confirmation.
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
