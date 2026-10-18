# Workflow and records redesign

**Accepted:** the owner approved this redesign on October 16, 2026. This contract
supersedes the normal catalog-only intake and separate Shows/Media navigation of
the October 14 audit. It does not supersede preservation of provenance, device
qualification, channel isolation, or the distinction between plans and airing.

## Accepted task contracts

- Work is the starting point for incoming files and unfinished work, prioritized
  by approaching airtime. Running jobs are separate from actions needing the
  owner. An unscheduled episode or unfinished intake must remain discoverable.
- Library owns shows, episodes, and reusable media. An episode opens to its
  upcoming premiere/replay dates and times, followed by files, preparation,
  publication, and history. Lists show premiere and next play. Unconfirmed dates
  are labeled suggestions and never create assignments merely by being viewed.
- Normal intake selects actual local files at their existing locations, inspects
  them, suggests editable identification and dates, and continues through
  preparation and reviewed device publication. Source media is never copied.
  No-file episode entry is an administrative exception.
- One intake can contain several files/shows. Persist review choices and progress;
  allow planning before preparation finishes; reuse completed work safely.
  Encoding and transfer use configured tools, profiles, and qualification gates.
- Corrected media is a new version within the existing episode. Preserve old
  files and historical evidence; preview changes to future plays and publish the
  resulting schedule revisions explicitly.
- Episode and calendar schedule edits use the same records. A one-off change is
  an exception, preserved by default when the parent cycle changes. Moving or
  cancelling a play must not let later materialization recreate the original.
- Recurring-time changes choose an effective date, preview affected future plays,
  preserve history and exceptions, expose conflicts, and require republication
  where existing device evidence becomes stale.
- Archive shows and episodes recoverably only after future uses and active jobs
  are resolved. Restore never recreates cancelled plans or restarts work.
- Maintain episode numbers and producer contact information in the application.
  History identifies the affected episode and links to its record. Audit access
  must be scoped to the working channel, including legacy records.
- Publication reviews supported pending changes together. Unsupported entries
  remain visible with an explanation. Sent, activated, independently verified,
  and evidence of actual airing remain different facts.

## Source audit and reproduced defect

An isolated in-memory test reproduced this sequence on the pre-change baseline:
create a two-play cycle, move a replay and choose a different episode, then save
the original cycle. The replay's episode was overwritten and the old play was
recreated (two planned rows became three). The same inspection confirmed the
episode editor omitted episode number and the requested audit history tab fell
back to Uploads. No operational records or device operations were used.

## Verification contract

The implementation must cover task completion, stale reviews, duplicate submits,
missing/moved sources, failed jobs, corrected versions, schedule exceptions,
effective recurring changes, archive/restore, channel isolation, and past evidence
preservation. Device behavior uses deterministic adapters during automated tests.
A synthetic desktop browser walkthrough checks normal entry points, empty/error
states, and keyboard use. The owner confirmed desktop-only operation; mobile
viewport testing is outside this task. Actual hardware qualification is separate;
software checks alone cannot establish a real controller publication succeeded.

**Open:** physical-device qualification remains dependent on a configured target
and its approved evidence. No release, source-media relocation, external hosting,
or additional vendor capability is authorized by this redesign.

Implementation validation results are recorded with the pull request after the
checks run; this acceptance document is not a claim that validation has passed.
