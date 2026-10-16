# Connected workflow implementation

**Accepted:** the owner authorized product implementation of this audit and reaffirmed it as the governing contract on October 15, 2026. The original findings and proposal remain the design record. This document tracks the resulting product change, not a second audit.

## Scope

- Media Library remains the normal entry point. Catalog intake distinguishes new episodes, existing episodes, and reusable media. Saving names the record and offers independent preparation and planning actions.
- Preparation activity defaults to monitoring. Contextual setup and existing reviewed preparation controls retain their source, destination, and confirmation protections.
- Schedule defaults to Week plan. Capacity remains available. Whole-week review includes all planned item types and distinguishes unknown coverage, unassigned reservations, explicit No program choices, and intentional virtual-channel intervals.
- Review cycle delivery retains its narrower publication scope and the selected week, including prior-cycle replays within that week.
- Today derives actionable work and waiting states from existing records. Preparation and delivery remain separate facts; upload evidence is not airing evidence. Manual to-dos remain deferred; no task database or manual Done override is introduced.
- Shared changes align Library filters, simplify show actions and settings groups, distinguish Today from Day plan, add a keyboard skip link, and retain visible navigation states.

## Compatibility

Existing planning rules, producer order, premiere cycles, No program, carry-forward, fixed starts, provenance and history remain authoritative. Legacy intake URLs and POSTs remain compatible. One additive schema migration stores durable catalog-intake submission tokens and payload fingerprints; no existing operational data is rewritten. No release or device operation is part of this change. UI testing uses the isolated synthetic audit workspace.

## Validation

- PASS: 205 focused Django tests across daily actions, week/cycle review, workflow handoffs, intake/queue, station isolation, existing schedule/catalog workflows, premiere planning, and data foundations. Command: `.venv/bin/python manage.py test tests.test_daily_actions tests.test_cycle_review_context tests.test_schedule_preparation tests.test_week_review tests.test_workflow_handoffs tests.test_ux_overhaul tests.test_workflows tests.test_station_delivery_ux tests.test_reserved_schedule_and_catalog tests.test_bulk_premiere_planning tests.test_ux_data_foundation tests.test_media_queue tests.test_connected_media_intake tests.test_todo_workflows --noinput`.
- PASS: existing JavaScript intake draft-state behavioral test, new intake script syntax check, Django system check, migration consistency, and `git diff --check`.
- PASS: independent review of the complete change and corrections; no remaining findings. Review caught destination readiness conflation, future conflict navigation, and session-only save idempotency, all corrected before publication.
- PASS: running-app synthetic walkthrough of catalog-only reusable intake, saved-record confirmation, no-device recovery, scheduling before preparation, Week plan, full-week review, and derived Today actions. The walkthrough exposed a form validation ordering bug; the station is now assigned before validation and a POST regression test covers it.
- PASS: Library alignment at desktop and 390-pixel width without horizontal page overflow; keyboard Tab shows visible focus and Enter on Skip to content focuses main.
- Limitation: the in-app browser did not provide a verifiable text-only zoom result. Narrow-window reflow was checked; enlarged-text behavior still needs a native-browser accessibility pass. Physical encoding, transfer, and device activation were not exercised; existing guarded paths are retained and tested through isolated fixtures.
- No release or package build was requested. No repository lint/type-check command is configured for this scope. Existing migration tests in `test_todo_workflows` leave the schema at an older version, so that module runs last in the focused command.
