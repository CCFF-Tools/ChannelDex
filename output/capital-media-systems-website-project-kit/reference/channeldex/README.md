# ChannelDex

ChannelDex, owned, developed, and published by Capital Media Systems, is a private, local application for planning a television station's full
broadcast day. It brings the program catalog, episode queue, recurring airtimes,
one-off schedule items, media-preparation notes, schedule-upload records, and
broadcast history into one place.

It is designed for one PUB-TV owner/editor using one Mac. It runs on that Mac and
opens in a web browser; ordinary use does not require cloud hosting or an internet
connection.

> **Current status:** ChannelDex is an implemented, locally tested V1 application, not a signed or
> notarized product release. The current packaged build targets Apple Silicon on
> macOS 26.x. Intel compatibility, installer behavior, signing, notarization, and
> general distribution have not been validated.

## What ChannelDex does

- Plans the complete 24-hour day, including programs, episodes, station IDs,
  PSAs, filler, reusable assets, and timed live events.
- Shows day, Monday-through-Sunday week, and agenda views with reserved and
  available time.
- Warns about overlaps, programs that exceed their fixed slots, and gaps shorter
  than the 60-second ideal. It does not silently move, trim, or fill anything.
- Keeps an ordered upcoming-premiere queue for each show.
- Plans an episode from its premiere through the following replays until the next
  premiere cycle begins.
- Records deliveries, media references, workflow milestones, preparation facts,
  device programming, and exact uploaded schedule revisions.
- Keeps planned schedules separate from reports or log-backed evidence that
  something actually aired.
- Retains an audit trail for important changes.

ChannelDex records and organizes the owner's work. It does **not** fetch files from
Dropbox, email, or SMB; encode or move media; upload schedules; control broadcast
equipment; confirm that content aired; or publish a public calendar.

## Starting and stopping ChannelDex

### Packaged Mac application

If you have been given a local `ChannelDex.app` build:

1. Open `ChannelDex.app`.
2. Wait for ChannelDex to open in the default browser.
3. Keep the application running while you use the browser interface.
4. When finished, use **Quit ChannelDex** in the upper-right corner of the page.

Closing the browser tab or window does not stop ChannelDex. You can also terminate the
application from the Dock or Activity Monitor. The packaged application listens
only on this computer at `127.0.0.1:8000` by default.

The package is currently unsigned. macOS may prevent it from opening depending on
local security policy; distribution and installation guidance are still open work.
Developers can build and run the current source by following the
[developer guide](docs/DEVELOPMENT.md).

## First-time setup

On the first launch:

1. Select **Settings → Station** and create the PUB-TV station record. Station
   time is interpreted in Eastern Time (`America/Detroit`).
2. Select **Settings → Devices** and add each playback or scheduling target that
   needs to appear in operational records.
3. Select **Catalog → Shows**, add a show, and then set its weekly airtimes from
   the show workflow.
4. Add the show's producer and episodes. Put pending episodes in the order the
   producer wants them to premiere.

Producer contacts, submission references, and storage paths are private local
metadata. ChannelDex does not send them to another service.

## Everyday workflow

### 1. Maintain the catalog

Open **Catalog → Shows** to add or update shows and their recurring time slots.
Open a show to add episodes and review its pending producer-directed queue. Lower
queue numbers premiere first unless the producer directs otherwise.

An episode page keeps several kinds of information deliberately separate:

- delivery details describe how the submission was made available;
- media references identify source and encoded files without storing the files;
- workflow stages record manual progress from received through scheduled;
- planned occurrences show intended broadcast use; and
- broadcast history provides separate evidence about what aired.

### 2. Plan a premiere cycle

Use **Schedule → Plan next premiere** to choose the episode for a show's next
premiere. ChannelDex may suggest the next pending episode and next configured premiere,
but the owner must confirm the choice.

Replays use the episode from the most recent premiere until the next premiere. The
cycle does not reset on Monday: for example, a Monday replay before a Wednesday
premiere still belongs to the preceding premiere cycle. If there is no new
premiere, choose an older episode explicitly; ChannelDex never makes that assignment
silently.

### 3. Complete the broadcast day

Use **Schedule → Day** or **Week** to review reserved and available time. Add
programs, IDs, PSAs, filler, live events, or other items with **Add schedule item**.
Program start times remain fixed.

The calendar distinguishes:

- **Reserved:** a planned item or active recurring reservation;
- **Available:** time outside reservations; and
- **Virtual-channel filler:** a reserved remainder after shorter known content.

A plan is only intent. It does not mean that the day was uploaded or aired.

### 4. Record preparation and scheduling work

Open **Schedule → What needs scheduling today** for planned items that do not yet
have an active upload record covering their exact current revision. For each item,
record preparation and device-programming facts as the real work is completed.

When the external schedule has actually been uploaded, use **Record schedule
upload** and identify the device and every occurrence revision it covers. Editing
a covered schedule item makes that older coverage stale; record a new upload for
the revised item. An upload can later be superseded or invalidated without erasing
its history.

### 5. Record what actually happened

Use **Operations → History and workflow** to review plans, uploads, audit events,
and airing evidence. A sighting or producer report is stored as an attributed
report. A checked device log can be stored as log-verified evidence. Corrections
remain linked to the earlier record instead of replacing history.

Scheduled, reported, and log-verified are different states. None is silently
promoted to another.

## Data and backups

The packaged application stores its database and supporting runtime data in:

```text
~/Library/Application Support/ChannelDex
```

For upgrade compatibility, if that new directory does not yet exist and the
legacy `~/Library/Application Support/PUB-TV` directory contains
`pubtv.sqlite3`, ChannelDex continues using the legacy directory. An explicit
`PUBTV_DATA_DIR` setting still takes precedence.

ChannelDex stores records and file references, not the source or encoded media itself.
Backups are manual in V1:

1. Quit ChannelDex and confirm it is no longer running.
2. Copy the selected active data folder to the chosen private backup location.
3. Keep the backup access-controlled because it can contain contacts and private
   operational metadata.

Do not copy only the visible SQLite file while the application is running. A live
copy can miss SQLite sidecar data and other files needed for a consistent restore.
Restore procedures have not yet been packaged as an end-user feature, so retain an
untouched backup before attempting a manual restore.

## Important limitations

- One owner/editor on one computer; there are no user accounts or shared-LAN mode.
- No automatic file download, network mount, transcoding, or media validation.
- No Leightronix or TelVue schedule exchange and no broadcast-device control.
- No public calendar or calendar export.
- No automatic proof of airing.
- No signed/notarized installer or validated Intel Mac build.

For implementation details, local source setup, tests, packaging, and the product
contracts, see the [developer guide](docs/DEVELOPMENT.md).
