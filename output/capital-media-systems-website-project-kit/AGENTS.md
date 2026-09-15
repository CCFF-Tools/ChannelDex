# Capital Media Systems corporate website guidance

Capital Media Systems is the developer, publisher, and owner of ChannelDex. This repository is for its public corporate website, not for the ChannelDex application.

## Current phase

The project is in planning and contract definition. `docs/PRODUCT_PLAN.md`, `docs/ARCHITECTURE.md`, `docs/CONTENT_MODEL.md`, `docs/BRAND_GUIDELINES.md`, `docs/DECISIONS.md`, and `docs/WEBSITE_HANDOFF.md` are the website source of truth.

Do not implement or publish the site until the owner confirms the approval gate in `docs/DECISIONS.md`. After approval, record the date and exact decisions before implementation.

## Documentation truth

Label material as **accepted**, **proposed**, or **open**.

- **Accepted** material is established by supplied creative masters, confirmed owner direction, or accepted ChannelDex documentation.
- **Proposed** material is a website, narrative, design, or technical recommendation awaiting confirmation.
- **Open** material requires owner input or verified real-world data.

Never turn proposed company history, product history, contact information, compatibility, availability, customer, certification, or performance claims into public facts without approval.

## Brand source of truth

Use only the current materials under `creative/`.

- The browser guide at `creative/Brand Guide/dist/index.html` is the current written baseline.
- Supplied master artwork under `creative/Finished Creative Assets/` controls logo geometry, proportions, color order, wordmarks, rules, endorsements, and descriptors.
- Do not use assets from an older repository `output/`, `tmp/`, or working-files directory.
- Do not recreate supplied logos with ordinary typography.
- Capital Blue (`#00529B`) belongs to Capital Media Systems; Station Blue (`#234C74`) belongs to ChannelDex.

## Product truth

Use `reference/channeldex/` when writing product claims. Preserve these boundaries:

- ChannelDex is television programming management software and the primary Capital Media Systems product.
- It supports schedules, catalogs, episode planning, media references, preparation records, and confirmed history.
- Planning is not proof of airing.
- Operator judgment remains visible; there is no silent program or episode assignment.
- Do not imply automatic playout control, transcoding, media fetching, device monitoring, vendor integrations, public hosting, or AI functionality.
- ChannelDex is local-first and does not require an external host, cloud runtime, CDN runtime dependency, or AI runtime.

## Creative direction

The corporate website is bright, capable, institutional, durable, and slightly dry. It carries the faintest secondary sense that Capital Media Systems is unusually committed to continuity.

Create that secondary reading through language, omission, disciplined archive structure, and institutional permanence. Do not use glitches, scan lines, CRT effects, fake aging, VHS noise, terminal theater, neon, cyberpunk, horror imagery, or overt conspiracy devices.

ChannelDex must feel historically continuous and actively maintained. It must never look like a simulated 1986 application or a modern reconstruction of a lost product.

## Technical invariants

- Produce a conventional self-hostable static site.
- Do not require ChatGPT Sites or any proprietary hosting platform.
- Do not require a cloud runtime, CDN, hosted fonts, analytics, tag manager, external tracker, or AI runtime.
- Host fonts and identity assets locally.
- JavaScript is progressive enhancement only.
- Essential content and navigation work without JavaScript.
- Target WCAG 2.2 AA.
- Preserve image aspect ratios, clear space, and meaningful alternative text.
- Keep deployment server-agnostic and document Caddy/nginx examples.
- Do not add a working contact form until its backend, privacy, retention, consent, and error-handling contract is approved.

## Scope boundaries

The first release does not include a CMS, database, authentication, analytics, e-commerce, licensing checkout, customer portal, live ChannelDex demo, support ticketing, or invented testimonials. These require separate approval.

## Working with existing files

Preserve the complete `creative/` and `reference/` trees. Do not modify master artwork in place. Place optimized deployment derivatives in the implementation's own public asset directory and document how each derivative was produced.

## Validation

Before declaring the website complete, run the affected tests and verify internal links, local asset resolution, third-party requests, keyboard behavior, focus, reflow, reduced motion, contrast, metadata, and self-hosted preview. Review every public factual claim against its accepted/proposed/open state.

