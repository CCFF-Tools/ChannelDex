# ChannelDex TODO and usability backlog

## UltraNEXUS automation — accepted 2026-09-29

- [x] Add versioned target settings, immutable evidence records, two approval
  snapshots, durable local jobs, per-item encode bypass, strict inspection,
  qualified AME/FFmpeg selection, verified staged FTP, NMG adapters, and the
  owner-facing Automation workspace.
- [ ] Qualify AME, FFmpeg equivalence, bypass, the NMG/BIN relationship,
  target-specific schedule.bin output, XPASS/LOADSCH activation, resource registration,
  interrupted publication recovery, rollback, DST, and midnight behavior using
  the evidence matrix in `ULTRANEXUS_AUTOMATION.md`.
- [x] Implement the restricted 7.0.3.48 BIN parser/writer, selected change
  manifests, attended FTP staging and XPASS activation, rollback journal, and
  experimental AME/FFmpeg comparison records. Hardware use stays qualified per
  target and individually confirmed.
- [x] Enforce the versioned restricted-target contract, exact template/profile
  identities, NMG/BIN logical parity, conditional new-resource registration,
  and the complete measured FFmpeg qualification matrix.
- [ ] Supply and review the private AME matrix outputs, then perform the
  destination-Mac/controller authentication, replacement, activation,
  observation, rollback, and representative WinLGX reconstruction run. These
  external qualification inputs remain the blockers to hardware enablement.

This is a durable implementation backlog. **Accepted** items are confirmed requirements; **proposed** items are design suggestions; **open** items still need an owner decision. A TODO entry is not evidence that the capability is implemented.

## Accepted usability overhaul — implementation staged 2026-09-17

The complete prior usability proposal is accepted for implementation. It is not
marked complete until code and pilot verification provide evidence.

- [x] Replace the current navigation with Today, unified Schedule (Day/Week/
  Agenda/Recurring Times), Shows, History, and Settings.
- [x] Add the schedule-item workbench and Show workspace; move actions into
  context, add inline supporting-record creation, progressive forms, and return
  users to their working context after save.
- [x] Add compatibility redirects from legacy routes and remove duplicate global
  creation/workflow destinations.
- [x] Derive status dimensions and one activity/preparation timeline from
  authoritative records; keep History principally read-only.
- [x] Implement canonical Producer ownership, asset filename, receipt facts,
  show-level slot setting with occurrence snapshots, asset/device transfer,
  occurrence/device programming, exact upload coverage, and derived episode
  labels. Retain legacy fields only as migration/audit metadata.
- [x] Migrate safely and auditably; do not delete provenance or silently merge
  plan/airing, reservation/occurrence, notice/receipt, source/encoded,
  slot/runtime, preparation/programming/upload, queue/readiness, or
  reported/log-verified facts.
- [x] Execute focused behavioral tests and a synthetic-data pilot. Documentation
  decisions are complete; runtime implementation and verification are recorded by
  the 137-test suite and the isolated synthetic premiere/upload pilot.

## Accepted requirements

### Show metadata

- [x] Add optional show-level fields for Website, YouTube, Facebook, and Instagram.
- [x] Add a show-level **Primary delivery method** dropdown with exactly: `Google Drive`, `Dropbox`, `Email link`, `SMB Transfer`, and `Other`.
- This is a preferred/default metadata value only. It must not imply credentials, fetching, connectors, or external transmission. Actual delivery method and provenance remain recorded per delivery and may differ from the show preference.

### Episode, delivery, and operations workflow

- [x] Provide an obvious **Add episode** action from show detail, with the show preselected.
- [x] Provide episode list and episode detail views, including intended order/slate information.
- [x] From show/episode context, make it possible to link a delivered file/reference and its delivery/provenance metadata to the episode. Keep source and encoded renditions distinct; references remain metadata only.
- [x] Make schedule preparation clearly support selecting the episode for a premiere and applying that same selected episode to its replays, with older reruns chosen explicitly case by case by default.
- [x] Add an off-by-default global schedule setting that carries the most recent explicitly planned episode into otherwise unassigned current/future weeks. Label and audit generated cycles, preserve explicit episode and No program overrides, stop at No program, and never advance the pending-new queue.
- [x] Add discoverable per-occurrence workflow/preparation tracking for source availability, encoding, transfer, library registration, slot assignment, and uploaded schedule revision. These are manual facts and do not imply airing.

### Calendar

- [x] Deliver a usable private in-app calendar with day, week, and agenda views for the complete planned broadcast day. Show item type, premiere/replay context, readiness, and gap/overlap warnings while keeping planned intent distinct from airing evidence.
- Calendar export remains deferred to the near-term roadmap.

## Current usability notes

- “Add item” is for a schedule item; it is not the show or episode creation flow.

## Open follow-up items

- [ ] **Accepted follow-up:** Add owner-controlled database portability tools:
  export a self-contained SQLite database snapshot and open a selected different
  database. The portable database/bundle must carry the information required to
  identify its schema/version and be suitable for storage on a network share and
  later opening by one ChannelDex instance. Define and verify the safe workflow
  for network-share paths, including SQLite sidecars, locking, integrity checks,
  and protection against concurrent access; do not imply that a live shared
  multi-writer database is supported until that behavior is validated.
- [x] Add a **Duplicate episode** action. It opens the Add episode page for the same show and pre-fills the editable fields from the reference episode, while leaving the new episode as a separate record.
- [x] Investigate and fix the **Choose next premiere episode** action, which could return an Internal Server Error. The planner now validates missing dates and resolves legacy assignments by their show/week identity; regression coverage is included.
- [x] Make the optional **Asset duration** control expose editable hours, minutes, and seconds, matching the exact-runtime control now available for episodes. Continue storing the combined total in `runtime_seconds`.
- [x] Derive **broadcast-day information** from the canonical show, episode, and schedule-assignment records instead of requiring a second owner-entered field with duplicate identity data. Preserve any legacy duplicate value only as migration/audit metadata.
- [x] Rework the schedule header layout so the date navigation and primary **Add schedule item** action sit in a right-aligned row horizontally aligned with the Day, Week, Agenda, and Recurring times controls.
- [x] Align all weekly show times to a consistent right-hand time column. Keep the premiere/replay indicator in its own fixed-width area so the premiere button does not shift the time alignment.
- [x] Add an episode-level workflow summary derived from the canonical delivery, asset/preparation, programming, upload-coverage, and airing records. Replace milestone-only “Not started” displays while preserving per-occurrence Workbench detail.
- [x] Make **Open workbench** consistently available for every scheduled occurrence across Day, Week scheduled-items mode, Agenda, Today/needs-scheduling, Show, and Episode contexts. Capacity-only Week mode remains occurrence-free by design.
- [x] Remove the descriptor text “Set positions, then save. This works without JavaScript.” from the episode queue controls.

## Proposed implementation sequence

1. Show metadata and delivery-preference fields.
2. Episode list/detail and contextual episode creation.
3. Delivery/file-reference linking from episode context.
4. Schedule episode selection and per-occurrence preparation workflow.
5. Day/week/agenda calendar views and navigation.

## Open decisions

- Exact labels and validation rules for social links, including whether YouTube means a channel or a show playlist.
- [x] `Other` requires a companion free-text description.
- Whether one delivery may link multiple episodes/assets in the first UI, as the data model allows.
- Detailed calendar interaction behavior (dragging, editing, and conflict resolution) remains open; fixed starts and no automatic shifting remain accepted constraints.

## UX follow-up backlog

These items were accepted from the owner's hands-on review after the first operational build. Exact implementation details marked **proposed** may be adjusted during implementation without weakening the accepted user outcome.

### Navigation and application chrome

- [x] Move **Quit ChannelDex** out of the primary navigation flow. Place it at the upper-right edge of the header with the ordinary navigation grouped on the left, and visually distinguish it from routine page links to reduce accidental use.
- [x] Reorganize the navigation around the owner's workflow. Not every route should be a top-level link. **Proposed information architecture:** Dashboard; Schedule (Day, Week, Agenda, Add item, Weekly episode planning); Catalog (Shows, Episodes, deliveries/media through contextual pages); Operations (History and upload workflow); Settings (Station and device). Preparation, programming, delivery, media-reference, and airing actions should be reached from the relevant show, episode, or scheduled item.
- [x] Complete a plain-language UX pass across the application: clear page purpose, field labels, short help text where needed, contextual primary actions, useful empty states, validation that explains how to recover, and an obvious next step after saving.

### Recurring show time slots

- [x] Replace weekday values `0`–`6` with day names ordered Monday through Sunday. Continue storing the existing zero-based weekday value internally.
- [x] Render start times with an appropriate local-time control. It must support typing and keyboard/stepper adjustment of hours and minutes and present 12-hour time. Owner-facing displayed times use lowercase `a.m.`/`p.m.` and spaced hyphens for ranges, such as `7:00 p.m. - 7:30 p.m.`. Continue interpreting the value in the station timezone.
- [x] Rename **Duration seconds** to **Time slot length** and use an hours-and-minutes duration control rather than asking the owner to calculate seconds. Store/convert the value without losing the existing duration data or second-level scheduling contract. Episode runtime additionally exposes editable seconds for exact media duration.
- [x] Replace the one-slot-at-a-time form with a simple show-level time-slot manager. A show may have multiple weekly air times, and exactly one active slot may be marked as the primary/new-episode premiere slot. Other active slots are replays of that week's explicitly selected episode. Example: Street Talk premieres Wednesday at 7:00 p.m. and replays Friday at 8:00 p.m.
- [x] Use the managed time slots plus the weekly episode selection to populate the planned schedule. Replays must use the same episode selected for that week's premiere; a week without a new episode continues to require an explicit older-episode/no-program choice.
- [x] Preserve historical slot definitions when schedules change. **Active from** and **Active until** currently provide effective dates so a future or seasonal slot change does not rewrite prior schedules. Hide these optional fields under an **Advanced / effective dates** section in the normal UI and explain their purpose; neither should be required for an ongoing slot.
- [x] Clearly label premiere versus replay occurrences in show, week, day, and agenda views.

### Episode entry and producer identity

- [x] Replace the invented free-text episode `producer_id` field with a relationship to a dedicated Producer record. ChannelDex does not currently assign producer IDs, so the owner-facing form must not ask for one.
- [x] Display and select producers by first and last name. Use an internal immutable application identifier only as an implementation detail; do not expose it as the producer's name or require the owner to type it.
- [x] Provide **Add producer** within or immediately beside the episode-entry flow, then return to the episode with the newly created producer selected. Preserve producer/contact privacy and provenance.
- [x] Keep an optional external membership-number field for a possible future MyTurn association. No MyTurn integration, lookup, credential, or membership-number requirement is part of the current product.
- [x] Rename and explain **Intended air order** in plain language. **Proposed label:** **Planned episode order**, with help text that lower numbers air first and producer direction may override the usual FIFO order.
- [x] Render **Intended premiere date** as an actual optional date picker.
- [x] Replace ambiguous **Received at** with **Episode received date and time**, an appropriate local date-time control, and help text explaining that it records when PUB-TV received or accepted the episode—not when it aired or was encoded.
- [x] Replace the speculative episode-status list with the accepted manual workflow stages: `Received`, `Downloaded`, `Encoded`, `Transferred`, and `Scheduled`.
- [x] Present a short mouseover tooltip or equivalent accessible help text for every workflow stage. **Proposed definitions:** **Received** means PUB-TV received the submission or delivery notice; **Downloaded** means the source file was copied into PUB-TV's working/storage workflow; **Encoded** means the required encoded rendition was produced and recorded; **Transferred** means the encoded file was transferred to the target playback system; **Scheduled** means the episode was placed in a recorded schedule revision. These definitions should be confirmed against the owner's exact manual handoffs during implementation.
- [x] Treat these as progressive, timestamped manual workflow milestones with provenance, while showing the latest completed stage as the concise episode status. Completing a later stage must not erase evidence of earlier stages.
- [x] Keep workflow status distinct from `Previously scheduled` queue classification and from airing evidence. `Scheduled` means planned/uploaded according to the recorded workflow; it is never proof that the episode aired.

### Date, time, and duration controls

- [x] Audit every form field that represents a date, local time, date-time, or duration. Use the appropriate native/framework-backed control and model/form type; do not expose storage formats such as integer seconds to the owner.
- [x] Render **Week start** as a date picker constrained to Mondays. Label it **Broadcast week (starts Monday)** and provide a sensible default for the current or selected schedule week.
- [x] Keep all scheduling interpretation in `America/Detroit`, independent of the Mac's current timezone, and retain the accepted fixed-start and DST-day rules.

The workflow-stage help copy above is implemented as **proposed** wording. Owner confirmation of the exact handoff wording remains open; changing that copy does not alter the accepted milestone data contract.

## Owner review follow-up: premiere cycles and daily work

- [x] Add an Eastern-time **Use current time** action beside episode received, delivery notice, and file received date-time fields.
- [x] Show the existing upcoming episode queue on Add episode, suggest the next order number, and clearly state when no upcoming episode is queued. Drag-and-drop remains an optional later enhancement.
- [x] Replace robotic episode-status copy with plain-language preparation and queue guidance while keeping workflow, queue classification, and actual airing history distinct.
- [x] Show the delivery **Describe other delivery method** field only when `Other` is selected, and clear stale other-method text when another method is saved.
- [x] Replace the owner-facing Monday-week and selection-type workflow with an owner-confirmed premiere-cycle plan that suggests the next pending episode and next configured premiere.
- [x] Model replay assignment from one premiere to the next: a replay before this calendar week's premiere belongs to the prior cycle, while replays after the premiere use the newly confirmed episode.
- [x] Present show actions with consistent button styling.
- [x] Store one show-level time-slot length and inherit it for every future premiere/replay occurrence instead of asking for it on each added weekly time.
- [x] Render planned, reserved, and available time as hours, minutes, and seconds rather than raw total seconds.
- [x] Add a **What needs scheduling today** view. It lists planned items without exact active upload coverage and removes them when that scheduling confirmation is recorded.

## Proposed calendar capacity and content classification

These additions are **accepted** and implemented.

- [x] Add Reserved/Available capacity intervals and filters to Day and Week. Available means outside planned occurrences and active recurring reservations; Agenda remains scheduled-items-focused.
- [x] Treat virtual-channel filler as reserved time inside a show reservation; virtual-channel switches are not manually scheduled at exact times. Missing runtime remains reserved with a “runtime not determined” label.
- [x] Add an optional controlled Show type: Arts and Culture, Religious, Opinion, Public Affairs, Government, Education, Community, Entertainment, Sports, Other. Other requires a description and clears when changed.
- [x] Make every calendar state accessible with text labels, a legend, sufficient contrast, and no color-only meaning.
- [x] Split Week into two non-redundant views: consecutive Available/Reserved capacity blocks, or individual scheduled items with consecutive Available blocks. Use rectangular capacity cards rather than oversized pill badges.
- [x] **Accepted:** Open the Week view in the continuous Available/Reserved capacity mode by default; keep the scheduled-items-plus-Available mode as an explicit choice.
- [x] Identify the recurring show inside reserved capacity blocks when no episode has been assigned yet; preserve the same label in Day capacity intervals.

### Resolved decisions

- Available is time outside both planned occurrences and active recurring reservations.
- Show type is one optional controlled value; calendar filters are on Day and Week only.
- State badges/legend provide text meaning; color is supplemental.

## Owner review follow-up: episode identity and queue control

- [x] **Accepted:** Rewrite the episode-detail “Where this episode stands” guidance in more natural owner-facing language. It still distinguishes preparation progress, the upcoming-premiere queue, and separately recorded broadcast history.
- [x] **Accepted:** Give every media asset a unique, stable **Asset ID** and a separately recorded **file name**. An episode commonly has at least one source-video asset and one encoded asset; both remain distinct, linkable records with their own identities and filenames.
- [x] **Accepted:** Keep the episode-level **Title** as the human-readable identity, separate from stable asset IDs and filenames. An episode may have multiple source and encoded assets, so no single asset filename replaces the episode title.
- [x] **Accepted:** Provide a visible upcoming-premiere queue that the owner can reorder directly with drag-and-drop (up or down). Persist the resulting planned episode order and make the order change auditable; producer direction remains authoritative. Keyboard-accessible reordering offers an equivalent path.
- [x] **Accepted:** Simplify the preparation forms around their canonical ownership boundaries, remove owner-facing provenance entry, and explain the Adobe Media Encoder preset/version, encoding machine/output notes, FTP destination, WinLGX library registration, and occurrence-specific WinLGX playback slot in operational language.

## Deferred automation roadmap (not part of the V1 implementation checklist)

- **Further down the road:** Optional Adobe Media Encoder MPC control could encode received files, transfer them by FTP after a successful encode, and confirm completion. This remains future local-workstation automation because no vendor API, credentials, or service availability is assumed.
- **Even further down the road:** Optional WinLGX MPC control or WinLGX schedule-file decoding could place transferred shows into a schedule. This remains outside the current product scope and assumes no vendor API, credentials, or service availability.

## Owner review follow-up: public-facing schedule and workflow clarity

### At-a-glance show schedule

- [x] **Accepted:** Add a simple upcoming reserved-slot schedule intended for showing other people what airs when. Show each upcoming reserved show slot at its full configured length using only the show name and time. Do not show episode names, Available intervals, preparation state, or other internal operational detail.
- [x] **Accepted:** Keep **Virtual-channel filler (reserved)** in the episode-specific operational schedule, where it identifies the remainder of a reserved slot after the episode ends. Do not render it as a separate interval in the show-title reserved-slot view because that view displays the full reserved show length as one block.

### Show and producer details

- [x] **Accepted:** Add a general show description supplied by the producer and display it in appropriate show-catalog/detail contexts.
- [x] **Accepted:** Replace the single general producer-contact value with separate private **Producer phone number** and **Producer email address** fields. Preserve existing contact data during migration as legacy contact text for owner review where it cannot be classified safely.
- [x] **Accepted:** On **Edit show**, show **Describe other show type** only when **Show type** is `Other`; hide it and clear stale text for every other selection.

### Preparation redesign

- [x] **Accepted:** Redesign preparation around episode- and asset-level facts that carry forward to every replay using the same exact episode/asset version. Occurrence-specific scheduling facts remain separate and are not copied when they do not apply.
- [x] **Accepted decision:** Preparation ownership is implemented as asset version (source/encoding), asset version + device (transfer/library registration), occurrence + device (playback programming), and exact occurrence revision (uploaded-schedule coverage). Replays reuse asset preparation but require independent occurrence programming/upload coverage; airing evidence stays separate.
- [x] **Accepted:** Remove the owner-facing **Provenance** field from the preparation workflow for now. Existing stored values remain as hidden legacy audit metadata; no historical values are discarded.
- [x] **Accepted:** Replace or clearly define unclear checklist language, including **WinLGX library registration** and **WinLGX playback slot**, using terms that match the owner's actual AME, FTP, WinLGX library, and scheduling handoffs.

### Uploaded-schedule workflow

- [x] **Accepted:** Remove generic object labels such as `Device object (x)` and `Occurrence object (y)` from every owner-facing control and page. Devices, occurrences, shows, episodes, assets, uploads, and related records now use concise human-readable names; internal numeric IDs remain implementation details.
- [x] **Accepted:** Remove the redundant **Preview and commit uploaded schedule** surface from the current manual workflow. Schedule coverage is recorded directly from the contextual **Record schedule upload** action in the schedule-item workbench; immutable coverage and audit history remain internal records.
- [x] **Accepted:** Make the Shows index sortable by title, code, type, episode count, or earliest weekly time, and show each active show’s weekly reserved times in a right-hand summary on its catalog card.
- **Deferred roadmap:** A future UltraNexus integration may directly propose schedule changes, preview the full change set and conflicts, and commit only after explicit owner confirmation. No available API, credentials, supported write interface, or authorization is assumed today.

### Plain-language queue labels

- [x] **Accepted:** Replace **Pending producer-directed queue** with the owner-facing label **Queued episodes**. Keep producer-directed ordering as a behavioral rule without repeating it in routine headings.
