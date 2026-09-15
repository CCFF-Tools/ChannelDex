# ChannelDex project guidance

ChannelDex is owned, developed, and published by Capital Media Systems. PUB-TV
remains the accepted first station boundary and compatibility identifier.

## Current phase

This repository is in planning and contract definition. Treat the product plan, data model, architecture, and decisions documents as the source of truth once they exist. Do not imply that a runnable app, deployment, integration, or production data pipeline exists. Implementation begins only after the user confirms the complete plan and approval gate.

An optional local routing reference may exist at `.codex/SUBAGENT_ROUTING.md` or in the agent environment. It is a convenience for local orchestration; no absolute workstation path is required by this project.

## Documentation truth

Label statements as **proposed**, **accepted**, or **open**. Proposed material is a design suggestion, accepted material is confirmed by the user or recorded decision, and open material requires resolution. Do not turn an assumption into a product invariant or report a planned capability as implemented.

## Product invariants

- Preserve confirmed broadcast history and its source and actor provenance; manual history entry is the initial path and future imports must be additive and auditable.
- Any future import preview must show field mapping, conflicts, and validation before commit, with idempotent operations and stable source identifiers.
- Do not assume vendor APIs, credentials, or service availability; adapters remain replaceable.
- PUB-TV is the first station boundary. GOV-TV is a later boundary and must not expand the initial product scope.
- The product must not require external hosting, a cloud runtime, a CDN runtime dependency, or an AI runtime.
- Protect privacy through data minimization, explicit access boundaries, and no unnecessary external transmission.
- Initial operation is one computer with one owner/editor. Do not overbuild roles or permissions; public access is deferred.
- Restrict the server to loopback by default. Shared LAN access and accounts are later options only if needed.
- V1 covers playlist preparation and media-management notes while retaining scheduling, catalog, and history as core scope. Leightronix file/schedule exchange is a future roadmap item, not an MVP prerequisite.
- Episode reuse follows a premiere-to-premiere broadcast cycle, not the Monday calendar boundary. A replay after a premiere uses that premiere's episode until the next premiere; a replay earlier in the calendar week than the premiere still belongs to the prior cycle. If there is no new premiere, staff choose an older episode case by case. The app may suggest the next pending episode and next configured premiere, but the owner confirms the plan; there is no silent assignment.
- V1 plans the full 24-hour broadcast day, including filler, station IDs, PSAs, live events, and programs. The app plans the whole day manually; it does not exchange schedules or control broadcast equipment.
- Reusable non-episode assets and timed live entries are first-class schedule items.
- Keep a complete planned day distinct from evidence that a day actually aired.
- Episode order follows producer direction, usually FIFO. Track intended air order and an optional intended premiere date per unaired episode; producer direction wins. After a premiere passes with a recorded uploaded matching schedule revision, classify the episode as previously scheduled and advance the pending queue idempotently; this is not proof of airing or automatic assignment. Cancelled or known failed premieres require owner review. Future premieres and those lacking a valid matching upload remain pending. Reruns do not consume the pending-new queue.
- Record submission source separately from storage destination. Dropbox, email, and local SMB references are private metadata only; do not require credentials, automatic fetching, connector downloads, network mounts, or V1 integrations.
- Manual V1 media workflow records source availability, Adobe Media Encoder preset/version/machine/output, exact FTP transfer/device, WinLGX library registration and slot assignment, and uploaded schedule revision. Source and derived encoded renditions remain distinct; app notes never imply automated validation or airing.
- Program starts are fixed: a 7:00–7:30 slot always starts at 7:00. The station returns to its virtual channel between programs for schedule, PSA slides, and rotating graphics; expected intervals are intentional and separately labeled from genuinely unknown coverage.
- A 60-second gap between programs is an ideal soft target. Flag episodes that exceed their fixed slot; do not automatically shift, trim, add filler, or control playout.

## Agent delegation

Use the cheapest adequate model and effort; these are project heuristics, not benchmark or capability promises. Honor fixed custom-role model assignments rather than overriding them, and prefer an exact specialized agent when one fits. Use generic explicit Terra only when no specialized role fits.

- Luna: low effort for mechanical or narrow discovery work; medium for bounded implementation when a specialized role fits.
- Terra: medium effort for ordinary multi-file reasoning and product or data contracts.
- Sol: low effort for focused review and medium for cross-cutting work; high only when needed.
- Astra: exceptional high effort only for unresolved architecture or high-consequence reasoning; do not make it the default.

Do not routinely escalate or spawn every tier. Conserve aggregate tokens as well as cost; do not manufacture tiny tasks. Keep forks small (`fork_turns="none"` when context is unnecessary), assign explicit non-overlapping ownership, and request reports of 300 words or fewer. Run the complete affected test set using the shortest practical command; do not skip relevant checks or add redundant reviews. Preserve concurrent edits.

The available project models are gpt-5.6-luna, gpt-5.6-terra, gpt-5.6-sol, and gpt-6-astra. Model positioning is based on the [official OpenAI model catalog](https://developers.openai.com/api/docs/models) (checked 2026-09-08).

Eastern Time is accepted, with `America/Detroit` as the proposed IANA identifier. The scheduling week starts Monday at 00:00 Eastern. The host platform is macOS. Private in-app day/week/agenda calendar views are V1; calendar export is explicitly deferred to a near-term roadmap follow-up.

## Approval gate

Before implementation, present the proposed plan, contracts, scope boundary, and open decisions for user confirmation. Proceed to implementation only after that confirmation. This gate exists because the user requested it.
