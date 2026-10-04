# Accepted UltraNEXUS plan: requirement check

**Accepted scope:** the two-approval automation plan supplied by the owner in
this conversation. This check uses the source after PRs #1–#6, rather than the
earlier conversational lists. Qualification is distinct from software tests.
Optional dashboards, general simulators, and additional registration scaffolding
are not new requirements of this work.

**Accepted local completion:** this change closes the software gaps identified
below: controller capture and signed exact review, guarded cancellation, bounded
FTP retry and owned-partial reconciliation, AME preset byte verification, and
explicit audited artifact retention. References to missing behavior describe the
audited baseline. They do not mark external qualification as complete.

| Original requirement | Source evidence and disposition |
|---|---|
| Two approvals; encode toggle enabled by default; changed inputs invalidate approval | `automation.py`, media queue and publication services. Implemented; the review must bind the exact displayed inputs before Approval 2. |
| Supervised durable local worker; retain ready media across schedule work | Launcher and `run_ultranexus_worker`, implemented by the independent media queue work in PR #6. Not a new gap. |
| Qualified AME preference, FFmpeg fallback, compatible byte-identical bypass | Encoder, inspection, and qualification adapters exist. Actual AME lifecycle, version compatibility, downmix/loudness/legalization and controller playback remain externally qualified; local tests cannot establish them. The preset's bytes must also match its recorded hash before AME starts. |
| Binary passive FTP, bounded timeouts/retries, progress, exact readback, no overwrite, job-owned partial recovery | Existing transfer adapter supplies hashing, passive binary transfer and progress states. Bounded transient retry and durable ownership of staging names were missing. Resume from an arbitrary byte offset is not required. |
| Fetch and preserve current controller state before preview | Snapshot import and delivery-time readback existed; automatic read-only capture before review was missing. A changed base must stop publication rather than imply that an unknown NMG/BIN rebase is supported. |
| Reconcile selected changes and present exact diff, media readiness, Switchbacks, removals, target and period | Generation and parity validation existed. The actual controller-versus-candidate review and its approval binding needed completion. Arbitrary controller-edit merging remains outside the qualified selected-change contract. |
| Cancel before schedule commitment without deleting prepared media | Status fields existed; a guarded cancellation action was missing. Cancellation must never race promotion or clear an uncertain operation. |
| NMG/BIN separation, exact bases, unknown bytes, duration/IDs/capacity, independent validation | Implemented by restricted parsers, mutation plans, permanent allocations and parity checks. Real golden fixture acceptance and representative WinLGX reconstruction remain external evidence. |
| Full-week authoritative replacement and cleared-base generation | **Open, externally gated.** The restricted implementation intentionally rejects this workflow. Requires paired clear-week/other-output captures and preservation acceptance tests. |
| Future approved delivery; revalidate at execution and never publish late | **Open, externally gated.** Future activation is explicitly rejected. No timer or local scheduler is counted as completion of controller timing semantics. |
| Activation, independent acceptance, actual airing distinguished; exact upload coverage | Attended publication and independent observation exist. Controller acceptance signals, competing-editor exclusion, activation and rollback on hardware remain qualified per destination. No airing evidence is inferred from FTP or command acknowledgement. |
| Keep bases, rollback snapshots, referenced revisions and latest 20 unpinned successful sets; explicit logged cleanup | No retention enforcement existed. Implement explicit managed-file cleanup with protected references and permanent audit metadata. |
| Research register and required failure tests | Existing register and tests cover restricted behavior; add tests for each closed software gap. External fixture, firmware, encoder, recovery and timing qualification remains open. |

## External evidence still required

**Open:** paired NMG/BIN captures and controlled add/move/replace/cancel/clear-week
changes; new-resource library/registration behavior; WinLGX send/acceptance and
related files; destination authentication, staging/promotion, competing edits,
interruption and rollback observations; AME lifecycle and paired FFmpeg outputs;
HD technical/duration fixtures; DST, midnight and activation-period behavior.
The acceptance matrix in `ULTRANEXUS_AUTOMATION.md` remains authoritative for
enabling each destination capability. Nothing in this check authorizes a live
controller test or passes a research gate.
