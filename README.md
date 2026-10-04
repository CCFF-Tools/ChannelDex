# ChannelDex end-user guide

ChannelDex is the private, local PUB-TV planning application from Capital Media
Systems. It runs on one Mac and opens in a browser. It records the station's
catalog, a complete 24-hour plan, manual media workflow facts, schedule-upload
records, and separately attributed evidence about what aired.

This guide describes the current owner/editor interface. **Accepted** describes
the V1 boundary and behavior the application preserves. **Proposed** describes
a future direction. **Open** means a release or product decision still needs
confirmation. A saved plan, preparation note, or upload record is never by
itself proof that a program aired.

## Safety and scope

The accepted V1 user is one owner/editor on one computer. The server listens on
loopback (`127.0.0.1`) and does not require cloud hosting, a CDN, an AI runtime,
accounts, or public access. PUB-TV is the only station boundary in V1; GOV-TV is
future scope. Dropbox, email, and SMB values are private references only:
ChannelDex does not fetch them, mount them, or transmit them.

The accepted media workflow records source availability, Adobe Media Encoder
preset/version/machine/output, exact FTP transfer/device, WinLGX library
registration and slot, and uploaded schedule revision. Source files and derived
renditions remain distinct records. Restricted UltraNEXUS preparation and
attended schedule publication are qualification-gated workflows; they do not
control playout or prove airing. A signed/notarized installer, Intel build,
restore wizard, public calendar, and calendar export are **open** or deferred.

## Start and stop

Open the packaged `ChannelDex.app`, wait for the default browser to open, and
leave the app running while using the browser. The default address is
`http://127.0.0.1:8000/`. Closing a browser tab does not stop the server. Use
**Quit ChannelDex** in the upper-right when finished.

The packaged app is currently unsigned; macOS security policy may prevent it
from opening. Developers can use [the developer guide](docs/DEVELOPMENT.md).

## Navigation at a glance

The header links are **Today**, **Schedule**, **Shows**, **Media Preparation**,
**History**, **Help**, and **Settings**. The queue strip below the header shows
active and attention counts.

* [Today](http://127.0.0.1:8000/) — current-day summary and attention items.
* [Schedule](http://127.0.0.1:8000/schedule/) — day, week, agenda, recurring times,
  review, and delivery.
* [Shows](http://127.0.0.1:8000/shows/) — catalog, show workspaces, episodes, and
  premiere planning.
* [Media Preparation](http://127.0.0.1:8000/media/) — intake, assets, preparation,
  and transfer notes.
* [History](http://127.0.0.1:8000/history/) — plans, uploads, audit events, and
  airing evidence.
* [Help](http://127.0.0.1:8000/help/) — in-app workflow instructions.
* [Settings](http://127.0.0.1:8000/settings/) — PUB-TV defaults and devices.

The URLs above are illustrative for this document. In the application, links use
Django named URLs so navigation remains correct if a path changes.

## First-time setup

1. Open [Settings](http://127.0.0.1:8000/settings/) and choose **Set up station**.
   The station name must be `PUB-TV`; timezone is `America/Detroit` (Eastern),
   even if the Mac uses another timezone.
2. In [Devices](http://127.0.0.1:8000/settings/devices/), add each playback or
   schedule target. The name should match the device's operational identity.
3. In [Shows](http://127.0.0.1:8000/shows/), create a show, then add its producer,
   episodes, and weekly times. Set exactly one active weekly time as the
   premiere slot.
4. Put pending episodes in producer-directed order. Lower **Planned episode
   order** numbers normally premiere first; producer direction wins.

## Everyday workflow

### Catalog and episode queue

Use [Shows](http://127.0.0.1:8000/shows/) to filter by show type and sort by
title, code, type, episode count, or earliest weekly time. A show workspace
contains metadata, weekly times, episodes, and **Plan premieres**.

Create a producer from **Shows → Add producer**. `First name` and `Last name`
are the display identity. `Phone`, `Email`, and `Notes` are private metadata.
`External membership number` is optional future association data; no lookup is
performed.

Create an episode with `Show`, `Title`, `Producer`, `Planned episode order`,
`Planned premiere date`, and `Episode runtime`. Runtime accepts hours, minutes,
and seconds: `00:27:30` means 27 minutes 30 seconds. The queue is intention,
not an automatic schedule.

An episode detail page can add workflow milestones. Choose `Received`,
`Downloaded`, `Encoded`, `Transferred`, or `Scheduled`; enter `Completed date
and time`, `Actor`, `Provenance`, and `Notes`. A `Scheduled` milestone
means a schedule revision exists, not that the episode aired.

### Plan premiere cycles

Open **Shows → Plan premieres**. Choose `Show`, a concrete `Premiere date`
(`2026-10-07`), and the `Episode for this premiere cycle`. The next pending
episode is suggested. Choose an older episode explicitly when needed, or leave
the episode blank for **No program**.

The premiere date must have active weekly times and exactly one active premiere
slot. A premiere cycle owns the episode from that premiere through its replays
until the next premiere; it does not reset on Monday. A Monday replay before a
Wednesday premiere belongs to the prior cycle. A 7:00–7:30 slot starts at 7:00.
A 60-second gap is an ideal warning threshold, not an automatic edit.

If **Keep the most recent episode in its weekly slots** is enabled, only
otherwise-unassigned current/future weeks receive visibly labeled carry-forward
plans. An explicit episode or **No program** wins; disabling it stops future
generation and does not delete existing plans. Carry-forward never consumes the
pending-new queue.

### Prepare the complete broadcast day

Use [Schedule](http://127.0.0.1:8000/schedule/) in **Day**, **Week**, or
**Agenda** mode. The plan covers all 24 hours, including programs, station IDs,
PSAs, filler, reusable assets, and timed live events. **Recurring times** shows
weekly reservations. **Available** is outside reservations; **Reserved** is
planned content or an active recurring reservation; virtual-channel filler is a
reserved remainder after shorter known content.

Choose **Add to schedule** to open the occurrence form:

* `Item type` chooses Episode, Station ID, PSA, Filler, Live, or another item.
* `Label` names non-episode items, for example `PUB-TV station ID 01`; episode
  labels are derived from the selected episode.
* `Show`, `Episode`, and `Asset` identify episode content. The asset must belong
  to that episode; Live items cannot have an asset.
* `Start date and time` uses `YYYY-MM-DDTHH:MM`, for example
  `2026-10-07T19:00`, interpreted in America/Detroit.
* `Planned length` uses hours/minutes (seconds are preserved when already
  present), for example 0 hours and 30 minutes.
* `Status` is planned schedule status, not airing evidence.
* `Reason` explains a cancellation, replacement, or other operator decision.

Program starts remain fixed. ChannelDex flags overlaps, short gaps, and an
episode that exceeds its fixed slot; it does not shift, trim, add filler, or
operate broadcast equipment. A complete plan is intentionally distinct from an
actual-airing record.

### Media Preparation

Open [Media Preparation](http://127.0.0.1:8000/media/). Its intake fields are
`Show`, `Destination device`, and the `Drop episode videos here or browse files`
chooser (multiple video, MXF, or MKV files). After each file is chosen, select
its `Episode`, or choose `Create a new episode` and enter `New episode title`.
The title is initially suggested from the filename, with underscores and hyphens
turned into spaces. Keep `Encode before transfer` checked unless an externally
encoded file is already qualified for that destination. Under `Next step`, choose
`Prepare and plan premieres` or `Prepare media only`, then choose `Review intake`.
The review lists each file, its episode/new title, position, and encode choice.
Confirmation creates a preparation batch once; it does not upload or activate a
controller.

An asset form displays the disabled `Asset ID` plus `Episode`, `File name`,
`Kind` (`source` or `encoded`), `Version` (for example `v2`), `Asset duration`
(for example 0:27:30), and `SMB reference`. The SMB value is a private
reference; no file operation occurs.

Asset preparation records `Source file available`, `Source available at`,
`Adobe Media Encoder preset`, `Encoding machine and output notes`, and `Encoding
completed at`. A transfer record records `Device`, `FTP transfer details`,
`WinLGX library registration`, `Transfer completed at`, and `Recorded by`.
Example FTP details: `ftp://controller.example:21/Vol1/mpeg/SHOW_E02.mxf`;
record the confirmation actually supplied by the operator, not a guessed path.

### Review schedule and delivery

Open **Schedule → Review schedule** to select confirmed premiere cycles and an
exact prepared video for a destination. The page fields work as follows:

* `Destination device` scopes the review to one target.
* Each cycle checkbox selects or clears that premiere cycle and its linked
  replays. `Episode video` chooses the reviewed, destination-prepared rendition
  for that cycle; it is never an implicit filename match.
* The results table's `Playback` column shows occurrence time, role, and
  revision. `Review` says why the item is included and whether it replaces media.
  `Existing media` shows the current occurrence choice. `Controller mappings`
  shows recorded publication/binary mappings, or says that none exists.
* **Review changes** creates a preview only. In the preview, the
  `I reviewed and authorize the listed replacements of existing media choices`
  checkbox is required when replacements are listed. **Confirm reviewed
  schedule plan** records the reviewed selection and queues the gated work.

Changing an occurrence, assignment, recurrence, selected asset, or destination
after preview makes the review stale; return to **Change selections** and create
a fresh review. Review and confirmation do not upload, activate, or prove airing.

Open **Schedule → Schedule delivery** for the attended, qualification-gated
UltraNEXUS workflow. In the advanced selected-changes publication form, the
fields are `Target` (the destination device), `Occurrences`, `Reconciliation
mode`, `Workflow mode`, and the hidden `Requested activation at`. A full-week workflow
requires reconciliation mode `authoritative`; `selected_changes` is the normal
manual mode. Scheduled activation is rejected: activation requires an attended
confirmation.

For each reviewed publication change, `Change` is one of `Add`, `Move`,
`Replace`, or `Delete`. `Source BIN slot` is an integer from `0` through `2999`.
`Source record SHA-256` is exactly 64 hexadecimal characters. For `Move`,
`Replace`, or `Delete`, it identifies the source record; `Add` has no source
record requirement. Staging/activation asks for a `Candidate SHA-256` and the
known-good hash shown by the page. Candidate and known-good values are exact
64-character SHA-256 hashes, not filenames.

The independent controller evidence file is JSON with required keys
`target_id`, `artifact_sha256`, `schedule_path`, `status` (must be
`observed_active`), `observed_at`, and `source`. The confirmation checkbox says
the operator observed that exact candidate active. Other explicit checkboxes
confirm staging, replacement/activation, or rollback; never check one without
performing that attended action. Research-dependent publication remains disabled
until controller evidence qualifies the target. After an external upload, use
**Record schedule upload**: choose `Device`, optional `External reference`,
`State`, `Supersession reason`, and every covered occurrence. Upload records do
not prove airing.

### Record what aired

Use [History](http://127.0.0.1:8000/history/) and the occurrence workbench to
record airing evidence separately. `Status` is either `Reported/observed` or
`Log verified`; `Source` says who or what supports it; `Aired date and
time` uses `YYYY-MM-DDTHH:MM`; `Notes` capture context; `Supersedes` links a
correction to earlier evidence. Scheduled status remains distinct from these two
airing-evidence choices.

## Settings and device settings

### Station Settings

The station form has `Name` (must be `PUB-TV`) and `Timezone` (accepted value
`America/Detroit`). **Schedule defaults** has two checkboxes:

* **Keep the most recent episode in its weekly slots until another episode is
  explicitly planned** enables the bounded, labeled carry-forward behavior.
* **Automatically pull the live controller schedule before publication
  preparation** controls a publication-preparation prerequisite. It does not
  mean unattended activation; attended confirmation remains required.

### Database portability

Settings can export a consistent, self-contained SQLite snapshot for saving to
a network share. To import one, choose the file in Settings; ChannelDex copies
and validates it locally against the current compatible ChannelDex schema, then
activates it only after you quit and reopen the packaged app. The current
database is retained as a recovery copy. Do not open one database concurrently
from multiple ChannelDex instances; a network share is not a live multi-writer
database.

### Add device

The device form has one field, `Name`: the exact playback or schedule-upload
device name, such as `WinLGX HD Master`. It is an identifier, not a hostname.

### Device Settings: connection

Each device has its own [Device Settings](http://127.0.0.1:8000/settings/devices/)
record. Editable fields are:

* `Controller host or IP address`: DNS host (`controller.example`) or IPv4
  address (`192.0.2.10`), without a protocol prefix.
* `Port`: TCP FTP port, normally `21`; valid range is 1–65535.
* `Controller username`: account name only. Leave the password blank to retain
  the saved macOS Keychain password.
* `Controller password`: replacement secret; never put it in documentation,
  URLs, or command lines.
* `Media directory`: remote directory, for example `/Vol1/mpeg`, using forward
  slashes and the exact qualified target directory.
* `Command port`: control/diagnostic port, normally `23`; valid range is
  1–65535. The current release does not expose a command secret for editing.

The schedule path is intentionally fixed to `/internal/schedule/schedule.bin`.
If a legacy value appears, review and resave it; do not substitute a local path.
The reconciliation display is read-only and currently preserves controller
records; unsupported controller modes are not exposed.

### Device Settings: local tools

`Adobe Media Encoder executable` is the full application executable path, for
example `/Applications/Adobe Media Encoder.app/Contents/MacOS/Adobe Media Encoder`.
`Adobe Media Encoder preset` is the reviewed `.epr` path, for example
`/Users/editor/Presets/Nexus Mono.epr`. `FFmpeg executable` is the exact
qualified executable path, for example `/opt/homebrew/bin/ffmpeg`. Use
**Browse** when possible. Saving these paths does not run an encode.

### Device Settings: evidence and diagnostics

`Base NMG path` identifies the private local qualified NMG copy. `Base BIN path`
identifies the private local qualified `schedule.bin` copy. `FFmpeg qualification
manifest` identifies the private Adobe Media Encoder/FFmpeg comparison manifest.
Example: `/Users/editor/ChannelDex/evidence/ffmpeg-manifest.json`.

The **Test FTP login and directory**, **Test Adobe Media Encoder application
and preset**, **Test FFmpeg and ffprobe**, and **Test evidence-file integrity**
buttons are bounded, read-only observations of displayed values. **Run all
read-only diagnostics** combines them. A result is stale as soon as settings
change. A matching fingerprint does not grant qualification; qualification is
explicit, versioned evidence approved separately. These settings do not enable
automatic encoding, upload, playout, or unattended delivery.

### Remaining form fields

For completeness, these are the labels used by the other owner-facing forms:

* **Show:** `Station`, `Title`, `Description`, `Show code` (a short stable
  identifier such as `morning-news`), `Show type`, `Describe other show type`,
  `Primary producer`, `Website`, `YouTube link`, `Facebook link`, `Instagram
  link`, `Primary delivery method`, and `Describe other delivery method`.
  Links are references, not fetched content. The delivery method is a
  preference; each actual delivery has its own provenance.
* **Weekly time:** `Station`, `Show`, `Weekday`, `Start time`, `Time slot
  length`, `Primary / new-episode premiere slot`, `Active from`, and `Active
  until`. Enter a clock time such as `07:00`; dates use `YYYY-MM-DD`. Leave an
  end date blank for an ongoing slot. Exactly one active slot should be the
  premiere slot.
* **Delivery:** `Method`, `Describe other delivery method`, `Reference`,
  `Delivery notice date and time`, `File received date and time`, `Notes`, and
  `Linked episodes`. A reference such as `Dropbox folder: /Shows/2026/E02` is
  metadata only; ChannelDex does not fetch it.
* **Programming:** `Device`, `WinLGX playback slot`, and `Note`. A slot such as
  `A-042` applies to this occurrence only and does not carry to a replay.
* **Schedule upload:** `Device`, `External reference`, `State`, `Supersession
  reason`, and `Occurrences covered`. Select the exact occurrence revisions
  included in the upload.
* **Airing evidence:** `Status`, `Source`, `Aired date and time`, `Notes`, and
  `Supersedes`. The source might be `operator observation` or `WinLGX log
  export`; it should explain why the evidence is trusted.
* **Occurrence edit:** `Item type`, `Label`, `Show`, `Episode`, `Asset`, `Start
  date and time`, `Planned length`, `Status`, and `Reason`, as described in the
  schedule section. The hidden revision check prevents overwriting a newer
  edit.

All date/time fields accept the browser's local control format
`YYYY-MM-DDTHH:MM` and are interpreted in the station timezone. Duration fields
use separate hour/minute/second controls, not a free-form string.

## Backups and data location

Packaged data normally lives in `~/Library/Application Support/ChannelDex`.
If that directory is absent and legacy `~/Library/Application Support/PUB-TV`
contains `pubtv.sqlite3`, the legacy directory remains usable. `PUBTV_DATA_DIR`
overrides both locations.

For a manual backup, quit ChannelDex, confirm it is no longer running, and copy
the active data directory to a private, access-controlled location. Do not copy
only the SQLite file while the app is running: SQLite sidecar data may be missed.
An end-user restore wizard is **open**; keep an untouched backup before any
manual restore attempt.

## What ChannelDex does not claim

It does not fetch submissions, prove airing, control playout, silently repair a
schedule, infer qualification, expose public access, or make a calendar export.
Future imports must be additive, idempotent, previewed with mappings/conflicts/
validation, and auditable before acceptance into the product boundary.

For contracts, see [Architecture](docs/ARCHITECTURE.md), [Data model](docs/DATA_MODEL.md),
[Product plan](docs/PRODUCT_PLAN.md), and [UltraNEXUS automation](docs/ULTRANEXUS_AUTOMATION.md).
