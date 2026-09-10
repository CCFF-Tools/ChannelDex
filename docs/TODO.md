# PUB-TV TODO and usability backlog

This is a durable implementation backlog. **Accepted** items are confirmed requirements; **proposed** items are design suggestions; **open** items still need an owner decision. A TODO entry is not evidence that the capability is implemented.

## Accepted requirements

### Show metadata

- [x] Add optional show-level fields for Website, YouTube, Facebook, and Instagram.
- [x] Add a show-level **Primary delivery method** dropdown with exactly: `Google Drive`, `Dropbox`, `Email link`, `SMB Transfer`, and `Other`.
- This is a preferred/default metadata value only. It must not imply credentials, fetching, connectors, or external transmission. Actual delivery method and provenance remain recorded per delivery and may differ from the show preference.

### Episode, delivery, and operations workflow

- [x] Provide an obvious **Add episode** action from show detail, with the show preselected.
- [x] Provide episode list and episode detail views, including intended order/slate information.
- [x] From show/episode context, make it possible to link a delivered file/reference and its delivery/provenance metadata to the episode. Keep source and encoded renditions distinct; references remain metadata only.
- [x] Make schedule preparation clearly support selecting the episode for a premiere and applying that same selected episode to its replays, with older reruns chosen explicitly case by case.
- [x] Add discoverable per-occurrence workflow/preparation tracking for source availability, encoding, transfer, library registration, slot assignment, and uploaded schedule revision. These are manual facts and do not imply airing.

### Calendar

- [x] Deliver a usable private in-app calendar with day, week, and agenda views for the complete planned broadcast day. Show item type, premiere/replay context, readiness, and gap/overlap warnings while keeping planned intent distinct from airing evidence.
- Calendar export remains deferred to the near-term roadmap.

## Current usability notes

- “Add item” is for a schedule item; it is not the show or episode creation flow.

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

- [x] Move **Quit PUB-TV** out of the primary navigation flow. Place it at the upper-right edge of the header with the ordinary navigation grouped on the left, and visually distinguish it from routine page links to reduce accidental use.
- [x] Reorganize the navigation around the owner's workflow. Not every route should be a top-level link. **Proposed information architecture:** Dashboard; Schedule (Day, Week, Agenda, Add item, Weekly episode planning); Catalog (Shows, Episodes, deliveries/media through contextual pages); Operations (History and upload workflow); Settings (Station and device). Preparation, programming, delivery, media-reference, and airing actions should be reached from the relevant show, episode, or scheduled item.
- [x] Complete a plain-language UX pass across the application: clear page purpose, field labels, short help text where needed, contextual primary actions, useful empty states, validation that explains how to recover, and an obvious next step after saving.

### Recurring show time slots

- [x] Replace weekday values `0`–`6` with day names ordered Monday through Sunday. Continue storing the existing zero-based weekday value internally.
- [x] Render start times with an appropriate local-time control. It must support typing and keyboard/stepper adjustment of hours and minutes and present 12-hour time with AM/PM where supported by the user's locale. Continue interpreting the value in the station timezone.
- [x] Rename **Duration seconds** to **Time slot length** and use an hours-and-minutes duration control rather than asking the owner to calculate seconds. Store/convert the value without losing the existing duration data or second-level scheduling contract.
- [x] Replace the one-slot-at-a-time form with a simple show-level time-slot manager. A show may have multiple weekly air times, and exactly one active slot may be marked as the primary/new-episode premiere slot. Other active slots are replays of that week's explicitly selected episode. Example: Street Talk premieres Wednesday at 7:00 PM and replays Friday at 8:00 PM.
- [x] Use the managed time slots plus the weekly episode selection to populate the planned schedule. Replays must use the same episode selected for that week's premiere; a week without a new episode continues to require an explicit older-episode/no-program choice.
- [x] Preserve historical slot definitions when schedules change. **Active from** and **Active until** currently provide effective dates so a future or seasonal slot change does not rewrite prior schedules. Hide these optional fields under an **Advanced / effective dates** section in the normal UI and explain their purpose; neither should be required for an ongoing slot.
- [x] Clearly label premiere versus replay occurrences in show, week, day, and agenda views.

### Episode entry and producer identity

- [x] Replace the invented free-text episode `producer_id` field with a relationship to a dedicated Producer record. PUB-TV does not currently assign producer IDs, so the owner-facing form must not ask for one.
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

### Resolved decisions

- Available is time outside both planned occurrences and active recurring reservations.
- Show type is one optional controlled value; calendar filters are on Day and Week only.
- State badges/legend provide text meaning; color is supplemental.

## Owner review follow-up: episode identity and queue control

- [x] **Accepted:** Rewrite the episode-detail “Where this episode stands” guidance in more natural owner-facing language. It still distinguishes preparation progress, the upcoming-premiere queue, and separately recorded broadcast history.
- [x] **Accepted:** Give every media asset a unique, stable **Asset ID** and a separately recorded **file name**. An episode commonly has at least one source-video asset and one encoded asset; both remain distinct, linkable records with their own identities and filenames.
- [ ] **Open:** Decide whether the episode-level **Title** remains a separate owner-facing field or whether the asset ID plus filename is the preferred identifying display. Do not remove or repurpose existing episode titles until this is decided; an episode may have multiple source/encoded assets.
- [x] **Accepted:** Provide a visible upcoming-premiere queue that the owner can reorder directly with drag-and-drop (up or down). Persist the resulting planned episode order and make the order change auditable; producer direction remains authoritative. Keyboard-accessible reordering offers an equivalent path.
- [ ] **Deferred:** Revisit the preparation checklist after the asset/queue work. Simplify or centralize fields that are normally constant for all shows (for example, the Adobe Media Encoder preset), and rewrite or explain fields that are unclear. This is explicitly not work for the current pass.

## Deferred automation roadmap

- [ ] **Further down the road:** Add optional Adobe Media Encoder MPC control to encode received files, transfer them by FTP after a successful encode, and confirm that the transfer completed. This is a future local-workstation automation; it assumes no vendor API, credentials, or service availability today.
- [ ] **Even further down the road:** Add optional WinLGX MPC control or decode WinLGX schedule files to place transferred shows into a schedule. This is a future integration; it is not part of the current product scope and assumes no vendor API, credentials, or service availability today.

## Owner review follow-up: public-facing schedule and workflow clarity

### At-a-glance show schedule

- [x] **Accepted:** Add a simple upcoming reserved-slot schedule intended for showing other people what airs when. Show each upcoming reserved show slot at its full configured length using only the show name and time. Do not show episode names, Available intervals, preparation state, or other internal operational detail.
- [x] **Accepted:** Keep **Virtual-channel filler (reserved)** in the episode-specific operational schedule, where it identifies the remainder of a reserved slot after the episode ends. Do not render it as a separate interval in the show-title reserved-slot view because that view displays the full reserved show length as one block.

### Show and producer details

- [x] **Accepted:** Add a general show description supplied by the producer and display it in appropriate show-catalog/detail contexts.
- [x] **Accepted:** Replace the single general producer-contact value with separate private **Producer phone number** and **Producer email address** fields. Preserve existing contact data during migration as legacy contact text for owner review where it cannot be classified safely.
- [x] **Accepted:** On **Edit show**, show **Describe other show type** only when **Show type** is `Other`; hide it and clear stale text for every other selection.

### Preparation redesign

- [ ] **Accepted:** Redesign preparation around episode- and asset-level facts that carry forward to every replay using the same exact episode/asset version. Occurrence-specific scheduling facts must remain separate and must not be copied when they do not apply.
- [ ] **Open:** Define which preparation facts belong to the episode, exact media asset/version, target device, or scheduled occurrence before changing the data model. The redesign must preserve audit history and must not treat preparation as proof of airing.
- [x] **Accepted:** Remove the owner-facing **Provenance** field from the preparation workflow for now. Existing stored values remain as hidden legacy audit metadata; no historical values are discarded.
- [x] **Accepted:** Replace or clearly define unclear checklist language, including **WinLGX library registration** and **WinLGX playback slot**, using terms that match the owner's actual AME, FTP, WinLGX library, and scheduling handoffs.

### Uploaded-schedule workflow

- [x] **Accepted:** Remove generic object labels such as `Device object (x)` and `Occurrence object (y)` from every owner-facing control and page. Devices, occurrences, shows, episodes, assets, uploads, and related records now use concise human-readable names; internal numeric IDs remain implementation details.
- [x] **Accepted:** De-emphasize **Preview and commit uploaded schedule** in the current manual workflow. It is no longer presented in primary navigation or dashboard actions while its final disposition remains open.
- [ ] **Open:** Decide whether the current page should be removed, renamed, or reshaped as a manual record of an upload. Preserve any existing audit/history data while this is resolved.
- [ ] **Deferred:** A future UltraNexus integration may directly propose schedule changes, preview the full change set and conflicts, and commit only after explicit owner confirmation. Do not assume an available API, credentials, supported write interface, or authorization today.

### Plain-language queue labels

- [x] **Accepted:** Replace **Pending producer-directed queue** with the owner-facing label **Queued episodes**. Keep producer-directed ordering as a behavioral rule without repeating it in routine headings.
