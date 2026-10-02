# UltraNEXUS automation contract and research register

Status: **accepted restricted implementation** on 2026-10-01. ChannelDex now
contains a target-specific BIN writer and attended staging/activation workflow.
Actual controller delivery remains blocked until the destination target's
research gates and separate operator confirmations are satisfied.

## Two approval boundaries

Approval 1 records the exact source path, SHA-256, asset revision, target, and
per-item **Encode before transfer** choice. The local worker may then encode with
a qualified Adobe Media Encoder bridge, or qualified FFmpeg when AME is not
available, probe the output, enforce the Nexus Mono profile, transfer it by FTP,
and verify the remote bytes. When encoding is disabled, the selected bytes are
unchanged and must pass the same profile inspection. An AME failure never causes
an automatic backend switch.

Approval 2 records the exact occurrence revisions and selected change operations,
controller snapshot, paired NMG/BIN hashes, target, and settings revision. It
never follows from Approval 1. Any changed input makes the
approval stale. Uploaded occurrence coverage is created only after independently
verified delivery and controller acceptance; neither FTP success nor schedule
approval is evidence that an item aired.

Independent activation evidence is a private JSON file containing `target_id`,
`artifact_sha256`, `schedule_path`, `status: "observed_active"`, `observed_at`,
and `source` (`controller`, `winlgx`, or `operator_observation`). ChannelDex
checks that these fields identify the acknowledged BIN and target before it
records uploaded-revision coverage.

The restricted release supports selected preserve-controller changes only.
Gaps appear as Switchbacks in NMG and are separately checked against executable
BIN events. NMG times use the qualified Thursday-based weekly-seconds epoch; the
parity report checks event identity, wrap-aware intervals, exact adjacency,
Switchbacks, and pre-existing intentional uncovered gaps. Full-week replacement
and scheduled activation remain disabled.
DST-transition weeks, midnight-crossing items, unresolved media, live items, and
unassigned episodes block publication.

## Implemented components

- Versioned target settings with macOS Keychain credential references.
- An exact restricted-target contract: UltraNEXUS-HD, firmware 7.0.3.48,
  output 1, Nexus Mono, qualified media-directory spelling, fixed schedule path,
  base/template/profile hashes, and destination evidence hash. A missing or
  changed value blocks generation and delivery.
- Immutable media probes, hashes, rendition lineage, controller bindings,
  transfer attempts, controller snapshots, NMG/BIN artifact revisions, activation
  evidence, and exact occurrence revision selections.
- A durable preparation queue and supervised `run_ultranexus_worker` command.
- AME ExtendScript and FFmpeg adapters, strict ffprobe validation, passive binary
  FTP with unique staging names and remote readback hashing, and deterministic
  27-character controller filename allocation.
- A hash-qualified Python NMG parser/writer for the reviewed 7.0.3.48 layout,
  including full-table blank/record validation, resource append, and Switchback
  replacement while preserving unknown bytes. NMG and BIN both serialize a
  shared immutable byte-mutation plan and retain exact changed-byte ranges.
- A separate fixed-layout BIN parser/writer with complemented CRC-32, exact
  target-base hash, selected changes, complete reparse, and byte-range manifest.
- Atomic NMG and BIN publication reparses the temporary on-disk artifact before
  creating an immutable revision; a failed write leaves the approved base and
  prior artifacts unchanged.
- Attended FTP staging, retained local and remote known-good BIN copies, XPASS
  authentication, one fixed LOADSCH command, ambiguous-result handling, explicit
  rollback, and independent activation evidence before upload coverage.
- An experimental AME/FFmpeg comparison workspace with explicit stream mapping,
  deterministic profile identity, `signalstats` video-level analysis, and
  `ebur128` loudness/peak analysis. FFmpeg execution requires a passed research
  gate and a schema-2 manifest whose geometry, frame-rate, audio, illegal-level,
  fractional-duration, loudness/limiter, and controller-playback cases have all
  passed owner review with no differences or unmeasured checks. Exact executable,
  preset, profile, and manifest identities must match.
- Target-scoped Media IDs and resource references are permanently reserved
  before transfer with database uniqueness and collision retry. A failed
  transfer does not recycle its reservation, while an exact retry reuses it.
- Permanent target-scoped Media ID/resource-reference reservations, seeded from
  existing bindings and checked against all retained qualified base versions.
- Owner-facing Automation settings, preparation batches, per-item encode toggles,
  schedule previews, and separate approval status.

## Research register

Research results are target-specific. Record them with a private evidence file;
the command stores its path and SHA-256 rather than copying operational fixtures:

```sh
python manage.py record_ultranexus_research DEVICE_ID KEY EVIDENCE_FILE --status passed
```

| Gate key | Required evidence and acceptance test | Unlocks |
|---|---|---|
| `ame_scripting` | Versioned AME lifecycle runs with the supplied hashed preset; unrelated jobs remain untouched; completion and failure are observable | AME encoding |
| `ffmpeg_equivalence` | AME/FFmpeg image, audio, duration, loudness, legalization, and controller playback comparison | FFmpeg fallback |
| `nexus_mono_bypass` | Accepted controller playback and technical comparison for already encoded files | Encode bypass |
| `nmg_bin_relationship` | Paired NMG/BIN captures with firmware, hashes, and controlled single changes | NMG/BIN transformation contract |
| `schedule_bin_format` | Accepted restricted 7.0.3.48 output-1 ordinary-video fixtures and destination validation | Restricted BIN generation |
| `schedule_bin_activation` | XPASS/LOADSCH target qualification, command responses, and independent activation evidence | Attended schedule activation |
| `ftp_promotion_recovery` | Interrupted staging, rename support, partial discovery, name races, concurrency, and rollback | Automated schedule publication |
| `resource_registration` | New-resource captures proving library files or registration operations | Publications whose BIN manifest introduces controller library resources; unchanged existing references do not require this gate |
| `dst_midnight_activation` | Vendor or controlled evidence for DST, midnight crossing, and activation-period rules | Those schedule cases |

The qualified active path is `/internal/schedule/schedule.bin`. Older target
settings using `/lgx-hdd/schedule/schedule.bin` require review and resave.
ChannelDex never renames NMG bytes to BIN. Hardware delivery is gated by the
exact target qualification and attended actions. Full-week replacement remains
unsupported.

The owner may record an experimental comparison with
`python manage.py record_ultranexus_ffmpeg_pair CASE SOURCE AME_OUTPUT FFMPEG_OUTPUT PRESET FFMPEG_EXECUTABLE AME_PROBE_JSON FFMPEG_PROBE_JSON --case-kind KIND --comparison-report-json REPORT --run-analysis`.
`KIND` must cover the schema-2 matrix documented above. The report records the
measured checks, differences, and unmeasured properties; metadata equality alone
cannot qualify a pair. `--run-analysis` executes `signalstats` and `ebur128` on
both outputs and retains compact numeric measurements without retaining raw tool
logs. Illegal-level, audio, and loudness cases cannot be accepted without those
paired measurements. The private manifest remains experimental until every
required case is accepted and its exact identities match the target settings.

For Nexus Mono, `duration_units` is exactly
`ceil(probed_duration_seconds × 30)` using decimal arithmetic. The compatibility
name `nominal_frames` remains in older probe evidence, but decoded frame count is
never substituted for this controller value.

Run the worker under a local supervisor after configuring target settings and
Keychain credentials:

```sh
python manage.py run_ultranexus_worker
```

`--once` is available for controlled tests and maintenance runs.

Captured state and independently validated artifacts can be attached without
editing the database:

```sh
python manage.py import_ultranexus_evidence snapshot DEVICE_ID snapshot.json
python manage.py import_ultranexus_evidence artifact BATCH_ID nmg schedule.nmg --validation-file validation.json
```

BIN validation evidence must identify the target, exact occurrence revisions,
and `source_nmg_hash` from which the BIN was produced. Approval 2 rehashes both
selected artifact revisions and rejects any mismatch.

Previously registered controller media stays a distinct attested path. After its
controller reference and private evidence are reviewed, record the attestation:

```sh
python manage.py attest_ultranexus_legacy_binding BINDING_ID EVIDENCE_FILE
```
# Station-centered Prepare & Deliver

The owner-facing entry point is `/automation/` (also labelled Prepare & Deliver). Source videos are submitted through Station Prepare, stored privately below `DATA_DIR/imports`, attached to a planned occurrence, and queued as a versioned preparation batch. The page derives five states—encode, media transfer, schedule insertion, schedule upload, and confirmation—from durable preparation, publication, delivery, and observation records. Creating a selected-change publication is idempotent and remains reviewable before attended activation.

UltraNEXUS device connection settings live in Station Settings. A device has one username and one opaque macOS Keychain reference shared by FTP and command delivery. Qualification hashes, template offsets, and recovery controls remain advanced records and are never treated as proof of airing.
