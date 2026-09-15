# Capital Media Systems corporate website handoff

Status: **Proposed website specification awaiting owner approval**  
Prepared: 2026-09-15  
Intended use: Attach this document and its adjacent `assets/` and `fonts/` folders to a new project. It is a complete creative, content, and technical brief for building the Capital Media Systems corporate website outside the ChannelDex application repository.

## 1. Executive brief

Build a polished, self-hostable corporate website for **Capital Media Systems**, the developer, publisher, and owner of **ChannelDex**.

Capital Media Systems should feel like an established Michigan broadcast-systems company with decades of institutional knowledge. ChannelDex is its current flagship product, but the company has produced other systems over the years.

The approved corporate tagline is:

> **PROGRAMMING A BRIGHTER TOMORROW**

The line should read as sincerely optimistic on first contact. On closer inspection, the company's flawless continuity, restrained language, and unusually complete institutional memory may feel faintly uncanny. The effect must remain subtle enough that an ordinary visitor can read the site as a confident corporate presentation.

ChannelDex must not look reconstructed from 1986, retrofuturistic, or artificially aged. It must feel as if **ChannelDex was already there**: continuously maintained, quietly familiar, and modern because it never stopped evolving.

## 2. Source-of-truth hierarchy

Use sources in this order:

1. The files packaged beside this document in `assets/` and `fonts/`.
2. The current ChannelDex Brand & Identity Standards Version 1.1 Draft in the source repository's `creative/Brand Guide/dist/index.html`.
3. Accepted ChannelDex product facts in `AGENTS.md`, `docs/PRODUCT_PLAN.md`, and `docs/DECISIONS.md` from the source repository.
4. This website handoff.

Do **not** use branding files from the source repository's `output/channeldex-brand*` directories, `tmp/` renders, or older working files. Those are not authoritative.

If a supplied master and written prose conflict, the current master artwork controls logo construction, proportions, color order, endorsement placement, rules, and descriptor relationships.

## 3. Truth labels

### Accepted

- Capital Media Systems owns, develops, and publishes ChannelDex.
- ChannelDex is the primary product.
- The formal product name is always **ChannelDex**.
- The formal introductory relationship is **ChannelDex by Capital Media Systems**, with ChannelDex visually dominant.
- The ChannelDex descriptor is **TELEVISION PROGRAMMING MANAGEMENT**.
- The Capital Media Systems corporate mark is based on the Michigan State Capitol dome.
- The approved corporate tagline is **PROGRAMMING A BRIGHTER TOMORROW**.
- ChannelDex is an established, technical, organized, durable television programming-management product for public-media environments.
- ChannelDex is local-first and does not require external hosting, a cloud runtime, a CDN runtime dependency, or an AI runtime.
- ChannelDex records plans and operational facts; a plan is not proof that something aired.
- The current raster identity masters are supplied in this package.

### Proposed

- The company was founded in 1986.
- The corporate history and timeline in this handoff.
- The former product names **FrameLine**, **Archive/6**, and **RelayWorks**.
- All copy describing those former systems.
- IBM Plex as the corporate website's supporting type family. It is supplied and is the current working recommendation, but the Brand Guide still labels supporting typography provisional.
- The multipage information architecture and dependency-free static implementation.
- The specific method used to create the subtle unsettling undertone.

### Open

- Real street address, telephone number, public email address, and business hours.
- Real staff names, titles, biographies, and photographs.
- Exact founding date and verified company chronology.
- Whether ChannelDex is commercially available, pilot-only, privately deployed, or presented as “contact us.”
- Pricing, licensing, support, procurement, and compatibility claims.
- Whether the fictional former products and dates are approved for publication.
- Final production domain and hosting environment.
- Whether a contact form will have an approved backend and privacy workflow.

Never silently convert a proposed or open statement into a public factual claim.

## 4. Brand architecture

### Parent identity

Capital Media Systems is the corporate identity. Use its independent mark on corporate pages, the global masthead, company materials, history, contact information, and the footer.

The corporate identity uses **Capital Blue**, not ChannelDex Station Blue.

### Product identity

ChannelDex leads on product pages. Use the endorsed ChannelDex lockup for formal product introductions and the product-only lockup only after the relationship has been established.

Do not combine the Michigan State Capitol dome with the ChannelDex symbol. Do not place ChannelDex's three records inside the corporate mark. Do not recolor ChannelDex with Capital Blue.

### Naming

- Correct: `ChannelDex`
- Contextual uppercase: `CHANNELDEX`, only when the surrounding layout is all uppercase
- Informal and internal only: `Dex`, `Dexter`
- Incorrect: `Channel Dex`, `ChannelDEX`

## 5. Canonical color system

| Token | Value | Corporate-site role |
| --- | --- | --- |
| Charcoal | `#181A1B` | Primary type, dark structure, rules, navigation, and dark fields |
| Capital Blue | `#00529B` | Parent-brand accent, corporate rules, links, and key corporate actions |
| Station Blue | `#234C74` | ChannelDex product accent, product information, and the upper record |
| Monitor Green | `#475F43` | ChannelDex middle record and restrained secondary product accent |
| Broadcast Red | `#7D2828` | ChannelDex lower record, “Dex” emphasis in supplied artwork, and selective high-emphasis product use |
| Warm Paper | `#F4E4CC` | Introductory fields, documentation-like surfaces, packaging references, and editorial panels |
| Near White | `#FFFDFA` | Primary page background |
| Warm Line | `#D6C4AA` | Dividers and low-contrast structure |
| Muted Ink | `#5D5A54` | Secondary prose and metadata |

Rules:

- Structure comes first; color behaves like a signal.
- Capital Blue and Station Blue are never interchangeable.
- Monitor Green is not automatically “success.”
- Broadcast Red is not automatically “error.”
- Color never carries status by itself.
- Use light text on Charcoal; the colored brand tones are not suitable for ordinary small text on Charcoal.
- Avoid arbitrary gradients. A restrained photographic overlay is permissible only when contrast and logo separation remain excellent.

## 6. Typography

Use the packaged IBM Plex fonts as the website's proposed working system:

- IBM Plex Sans Regular 400: body copy
- IBM Plex Sans Medium 500: navigation and display headings
- IBM Plex Sans SemiBold 600: section headings and controls
- IBM Plex Sans Bold 700: rare high-emphasis text
- IBM Plex Mono Regular 400: timestamps, identifiers, archive numbers, and technical metadata
- IBM Plex Mono SemiBold 600: short uppercase category and field labels

Typography rules:

- Never recreate either logo with website fonts.
- Display titles: 36-72 px, compact line-height, slight negative tracking, sentence case.
- Section headings: 24-40 px, SemiBold, sentence case.
- Body: 16-19 px, line-height 1.5-1.7, readable measure.
- Category labels: 12-14 px, Mono SemiBold, uppercase, tracking `0.08em` to `0.14em`.
- Technical data: 13-16 px, Mono, tabular numerals.
- Ordinary navigation and buttons are not set in all caps.
- Accessibility overrides brand sizing whenever needed.

## 7. Visual language

The website should feel like a modern corporate identity system shaped by broadcast operations, information design, institutional computing, technical literature, and durable equipment documentation.

Use:

- Measured columns and an explicit grid
- Thin horizontal rules
- Generous empty space
- Modular information panels
- Schedule structures, archive references, record rows, and metadata as quiet visual grammar
- Clean architectural or operational photography
- Cropped details of control surfaces, racks, labels, workstations, and well-maintained facilities
- Current ChannelDex interface imagery shown clearly on modern displays
- Restrained transitions that communicate hierarchy

Do not use:

- Yellow paper, stains, dust, scratches, or faux distressing
- CRT bloom, scan lines, terminal flicker, or phosphor effects
- VHS noise, tracking errors, or fake analog interference
- Pixel fonts, arcade references, synthwave, neon, or cyberpunk motifs
- Fake low-resolution screenshots
- Consumer-SaaS blobs, glossy gradient meshes, or floating emoji-like icons
- Government clip art
- Generic “people pointing at a dashboard” stock photography
- Constant use of all three ChannelDex record colors on unrelated corporate surfaces

The brand's historical character comes from proportion, discipline, language, and continuity—not simulated age.

## 8. Tone and narrative tension

### Surface voice

- Competent
- Organized
- Technical
- Institutional
- Quietly distinctive
- Slightly dry
- Durable
- Confident
- Optimistic without exuberance

### Subtle secondary reading

Create unease through immaculate institutional order and carefully controlled omissions. The company seems to remember everything, yet explains only what is operationally necessary.

Suitable techniques:

- A timeline entry that begins with ChannelDex already in service rather than announcing its invention.
- Archive identifiers that imply more records than are shown.
- Phrases such as “continuity is maintained,” “the record remains available,” or “every schedule has a history” used sparingly.
- Perfectly neutral descriptions of retired systems.
- A footer archive reference or revision date that resembles controlled documentation.
- Photography captions that are precise but curiously impersonal.

Avoid:

- Jump scares, hidden monsters, secret laboratories, biohazards, or overt conspiracy language
- Glitches or sudden visual corruption
- Fake warnings such as “unauthorized access detected”
- Anything resembling an ARG unless separately authorized
- Claims of surveillance, omniscience, coercion, or dangerous intent
- Jokes that undercut the established-company presentation

The site must still work completely as a sincere corporate website.

## 9. Information architecture

Build these routes:

| Route | Page title | Purpose |
| --- | --- | --- |
| `/` | Capital Media Systems | Corporate introduction and flagship-product presentation |
| `/channeldex/` | ChannelDex | Accurate, detailed flagship product page |
| `/systems/` | Systems Archive | Current and historical product family |
| `/company/` | Company | History, principles, staff/facility placeholders |
| `/contact/` | Contact | Public contact details and inquiry path |
| `/privacy/` | Privacy | Static privacy statement |
| `/accessibility/` | Accessibility | Accessibility commitment |
| `/404.html` | Page not found | Branded, useful error page |

Global navigation:

- ChannelDex
- Systems archive
- Company
- Contact

The Capital Media Systems mark links to the home page. The mobile navigation must be keyboard-operable and must not require JavaScript to expose essential links when scripting is unavailable.

## 10. Homepage content deck

### Metadata

**Title:** Capital Media Systems | Programming a Brighter Tomorrow  
**Description:** Capital Media Systems develops durable television programming and broadcast-information systems, including ChannelDex.

### Hero

**Eyebrow:** `CAPITAL MEDIA SYSTEMS / MICHIGAN`

Use the supplied corporate mark prominently with adequate clear space.

**Display statement:**

> Programming a brighter tomorrow.

**Introductory copy:**

> Capital Media Systems develops durable information systems for the people who keep television programming organized, prepared, and accountable.

**Primary action:** Explore ChannelDex  
**Secondary action:** About Capital Media Systems

Optional quiet metadata below the actions:

`BROADCAST INFORMATION / PROGRAMMING SYSTEMS / CONTINUITY`

### Introductory positioning

**Section label:** `BUILT FOR CONTINUITY`

**Heading:**

> Broadcast operations change. The record should remain clear.

**Copy:**

> Equipment is replaced. Workflows evolve. Programming still depends on knowing what belongs on the schedule, which material is ready, what was planned, and what has actually been confirmed. Capital Media Systems builds around that durable center.

Use a clean operational image placeholder with this specification:

- Aspect ratio: 4:3
- Subject: well-maintained local broadcast operations room or media workstation
- Lighting: bright, controlled, believable
- No neon, haze, fake scan lines, or illegible screen composites
- Caption: `OPERATIONS ENVIRONMENT / IMAGE PENDING`

### ChannelDex flagship feature

**Section label:** `PRIMARY PRODUCT`

Use `ChannelDex Full Logo Color.png` as the formal introduction.

**Heading:**

> Television programming management with a long memory.

**Copy:**

> ChannelDex brings schedules, recurring programs, episode planning, media references, preparation records, and confirmed broadcast history into one understandable operating record. It is designed for the practical reality of public-media work: fixed airtimes, changing equipment, manual judgment, and information that must remain trustworthy.

**Supporting statements:**

- Plan a complete broadcast day without confusing intent with proof of airing.
- Coordinate premiere cycles and replays while keeping episode selection under operator control.
- Track media preparation and device-specific readiness without pretending to automate external equipment.
- Preserve confirmed history with its source and provenance.
- Operate locally without a required cloud, CDN, or AI runtime.

**Action:** View ChannelDex

Image placeholder:

- Aspect ratio: 16:10
- Subject: real current ChannelDex interface on a modern desktop display
- Use a genuine application capture when available
- Do not place the UI inside a fake 1980s computer
- Caption: `CHANNELDEX / CURRENT APPLICATION VIEW / CAPTURE PENDING`

### Product continuum

**Section label:** `SYSTEMS / SELECTED RECORDS`

**Heading:**

> Tools change. The work continues.

**Copy:**

> Capital Media Systems has supported programming, media coordination, and operational continuity across successive generations of broadcast equipment. Selected systems are retained here as part of that working history.

Cards:

1. **ChannelDex** — Current / Television programming management
2. **FrameLine** — Archive / Continuity and station-graphics preparation
3. **Archive/6** — Archive / Media reference and labeling
4. **RelayWorks** — Archive / Transfer and readiness coordination

The three archived products are proposed fiction and must not be published without approval.

**Action:** Review the systems archive

### Corporate principles

**Section label:** `OPERATING PRINCIPLES`

Use three restrained columns:

**The schedule is a plan.**  
Planning deserves precision, but it is not evidence that a broadcast occurred.

**History requires provenance.**  
A durable record preserves where information came from, when it was confirmed, and who confirmed it.

**Operators remain in control.**  
Software can organize choices and expose conflicts. It should not silently make programming decisions.

### History preview

**Section label:** `THE CONTINUING RECORD`

**Heading:**

> Established systems. Maintained deliberately.

**Copy:**

> The Capital Media Systems archive documents changing formats, equipment, and working methods. The organizing purpose is remarkably consistent.

Proposed timeline preview:

- `1986 / CHANNELDEX IN SERVICE`
- `1992 / FRAMELINE RECORD ESTABLISHED`
- `1998 / ARCHIVE/6`
- `2006 / RELAYWORKS`
- `CURRENT / CONTINUITY MAINTAINED`

**Action:** Read the company history

### Closing statement

Charcoal field, light text, no glitch treatment.

> The next program begins at a known time. Everything before it is preparation.

**Action:** Contact Capital Media Systems

## 11. ChannelDex page content deck

### Metadata

**Title:** ChannelDex | Capital Media Systems  
**Description:** ChannelDex is a local-first television programming-management system for schedules, program catalogs, media preparation, workflow records, and confirmed history.

### Hero

Use `ChannelDex Full Logo Color with Tagline.png` on Warm Paper or Near White.

**Eyebrow:** `TELEVISION PROGRAMMING MANAGEMENT`

**Heading:**

> One operating record for the programming day.

**Copy:**

> ChannelDex organizes the information between a program decision and a completed broadcast record. It supports scheduling, episode planning, media references, preparation notes, and history while keeping operator judgment visible.

Do not add a “Buy now,” “Start free,” pricing, or download action. Until availability is approved, use **Discuss ChannelDex**.

### Product model

**Heading:**

> Programming does not happen in the abstract.

**Copy:**

> It happens at a time, on a channel, with a program, an episode or reusable asset, a preparation state, a target device, and a history. ChannelDex keeps those relationships legible without turning a working schedule into an unsupported claim about what aired.

### Capability sections

**Schedule**  
Plan the full 24-hour broadcast day, including programs, station identification, public-service announcements, filler, and live events. Fixed starts remain fixed. Gaps, overlaps, and content that exceeds its slot are exposed rather than silently corrected.

**Programs and episodes**  
Maintain recurring program information, reserved airtimes, episode queues, premiere cycles, and replays. ChannelDex may suggest the next pending episode, but the operator confirms the plan. Producer direction wins.

**Media and preparation**  
Reference source and encoded media separately. Record availability, encoding details, transfer destinations, library registration, slot assignment, and uploaded schedule revisions without claiming to perform those external steps automatically.

**History**  
Keep planned occurrences distinct from source-backed airing evidence. Corrections retain provenance so the record can become more accurate without becoming less accountable.

### Local-first section

**Section label:** `LOCAL OPERATION`

**Heading:**

> The station's information remains at the station.

**Copy:**

> ChannelDex is designed to operate on one computer with locally served interface assets and no required external hosting, cloud runtime, CDN dependency, or AI service. Private operational metadata does not need to be transmitted elsewhere for the application to work.

Avoid implying encryption certifications, formal security audits, multi-user permissions, public access, automatic backups, or integrations that have not been approved.

### Product closing

> A clear plan for tomorrow. A trustworthy record of what is known.

Action: **Discuss ChannelDex**

## 12. Systems Archive content deck

### Page framing

This page makes Capital Media Systems feel established without turning into retro cosplay. It resembles a maintained corporate product archive, not a museum exhibit.

**Eyebrow:** `SYSTEMS ARCHIVE / SELECTED RECORDS`

**Heading:**

> Systems retained in the company record.

**Intro:**

> Capital Media Systems has developed tools around a consistent operational problem: keeping programming information understandable while formats, equipment, and workflows change.

### Current system

**ChannelDex**  
Status: Current  
Category: Television programming management

> The primary Capital Media Systems product for program catalogs, schedule planning, episode cycles, media preparation records, and confirmed history. ChannelDex is presented as a continuing system, not a revival.

### Proposed archived systems

These entries are invented brand history and require approval before publication.

**FrameLine**  
Proposed record: 1992-2004  
Category: Continuity and station-graphics preparation

> FrameLine organized the recurring slates, identifiers, and visual continuity elements used between scheduled programs. Its emphasis was consistency across repeated production tasks.

**Archive/6**  
Proposed record: 1998-2012  
Category: Media reference and labeling

> Archive/6 linked physical media, shelf references, program records, and operator notes during a period when several storage formats routinely coexisted.

**RelayWorks**  
Proposed record: 2006-2018  
Category: Transfer and readiness coordination

> RelayWorks recorded the handoffs between preparation workstations, transfer destinations, device libraries, and programming staff. It documented readiness without claiming to control the equipment it described.

Closing line:

> Retired software leaves production. Its records do not need to disappear.

## 13. Company page content deck

### Hero

Use the Capital Media Systems corporate mark.

**Eyebrow:** `COMPANY / MICHIGAN`

**Heading:**

> Information systems for the people behind the signal.

**Copy:**

> Capital Media Systems develops broadcast-information tools around the realities of station work: fixed airtimes, recurring obligations, changing equipment, imperfect source material, and decisions that need an accountable record.

### Proposed origin story

This narrative is fictional until approved.

> Capital Media Systems was established in Michigan in 1986 around a practical observation: television operations accumulate information faster than they accumulate certainty. Schedules change, media moves, equipment is replaced, and the same questions return every week. The company began by organizing those questions into systems that station staff could operate and trust.
>
> Surviving company records from 1986 already refer to ChannelDex as the organizing system for program information. No launch announcement is retained. The system appears in the record fully named and in active use.
>
> In the decades that followed, Capital Media Systems developed adjacent tools for continuity, media reference, and transfer coordination. Some were retired as equipment and formats changed. ChannelDex remained.

The sentence about no retained launch announcement is the strongest uncanny note on the site. Do not intensify it elsewhere.

### Proposed timeline

**1986 / The operating record**  
Capital Media Systems is established in Michigan. ChannelDex is already documented in active programming work.

**1992 / Visual continuity**  
FrameLine extends the company's systems into station graphics and continuity preparation.

**1998 / Media reference**  
Archive/6 organizes physical-media references, labels, and associated program information.

**2006 / Readiness coordination**  
RelayWorks documents preparation and transfer status across changing workstation and playout environments.

**2018 / Consolidation**  
Legacy product lines close to new use. Their operational principles and retained records continue to inform ChannelDex.

**Current / ChannelDex**  
ChannelDex remains the primary Capital Media Systems product: historically informed, actively maintained, and built for modern station work.

### Principles

**Durability over novelty**  
Technology changes quickly. Operational meaning should not.

**Precision without automation theater**  
The system should represent what it actually knows and leave operator decisions visible.

**Privacy through restraint**  
Store only what serves the work. Do not transmit information unnecessarily.

**History with evidence**  
Planned, prepared, uploaded, reported, and confirmed are different facts.

### Staff placeholder

Do not invent real people.

Use cards labeled:

- `EXECUTIVE LEADERSHIP / PROFILE PENDING`
- `PRODUCT & SYSTEMS / PROFILE PENDING`
- `OPERATIONS / PROFILE PENDING`

Photography specification:

- 4:5 portraits
- Neutral or real workplace background
- Direct, competent, approachable expression
- Consistent lighting and crop
- No exaggerated startup poses or monochrome “mystery executive” styling

### Facility placeholder

Caption: `CAPITAL MEDIA SYSTEMS / MICHIGAN / LOCATION DETAILS PENDING`

Do not imply ownership of a particular building until a real facility image and location are supplied.

## 14. Contact page content deck

**Eyebrow:** `CONTACT / CAPITAL MEDIA SYSTEMS`

**Heading:**

> Start with the programming problem.

**Copy:**

> Tell us about the schedule, records, equipment, or workflow you are trying to make clearer. We will begin with what is known and identify what still requires a decision.

Until real details are provided, display visibly intentional placeholders:

- `PUBLIC EMAIL / PENDING`
- `TELEPHONE / PENDING`
- `MAILING ADDRESS / PENDING`
- `BUSINESS HOURS / EASTERN TIME / PENDING`

Do not ship a fake contact form. Use either verified contact links or a clearly labeled static inquiry placeholder. If a form is later added, define data retention, delivery, spam protection, consent language, error handling, and accessibility before implementation.

## 15. Privacy page content

Initial static version:

> This website is designed to provide information about Capital Media Systems and ChannelDex without requiring visitor accounts, advertising identifiers, or behavioral tracking. The initial site does not use analytics, tracking pixels, third-party advertising, or a data-collecting contact form.
>
> If website features later collect personal information, this notice must be updated before those features are made available.

Do not claim that server logs do not exist unless production hosting is configured to make that statement true.

## 16. Accessibility page content

> Capital Media Systems aims to make this website usable across devices, input methods, and assistive technologies. The implementation targets WCAG 2.2 AA, including keyboard access, visible focus, sufficient contrast, meaningful structure, useful alternative text, reduced-motion support, and information that does not depend on color alone.
>
> Accessibility concerns may be reported through the verified contact method listed on the Contact page.

## 17. Footer specification

Use the Capital Media Systems corporate mark at a modest size. Include:

- ChannelDex
- Systems archive
- Company
- Contact
- Privacy
- Accessibility
- Copyright year and Capital Media Systems
- `PROGRAMMING A BRIGHTER TOMORROW`

Optional archive-style footer line:

`CAPITAL MEDIA SYSTEMS / CORPORATE RECORD CMS-WEB-001 / CURRENT REVISION`

This line should be small and unobtrusive. Do not animate it.

## 18. Image and creative-asset plan

### Required eventual photography

1. Corporate exterior or workplace establishing image
2. Broadcast operations environment
3. Current ChannelDex interface on a modern workstation
4. Three consistent staff portraits
5. Detail image of labels, schedules, racks, or preparation equipment
6. Optional historical product packaging or media objects, only if they can be produced credibly

### Placeholder design

Placeholders are acceptable in the first build but must be art-directed:

- Use Warm Paper, Near White, or Charcoal fields.
- Include aspect ratio, asset category, and status in IBM Plex Mono.
- Use thin rules and measured internal spacing.
- Do not use generic gray rectangles, broken-image icons, or fake photography.
- Make replacement straightforward through a documented `<picture>` or image-component interface.

### Image behavior

- Supply meaningful `alt` text for informative images.
- Use empty `alt` for decorative images.
- Never repeat logo wording unnecessarily in adjacent accessible text.
- Use `width` and `height` attributes to prevent layout shift.
- Prefer AVIF/WebP derivatives for photography with a conventional fallback.
- Keep original master PNGs untouched.

## 19. Supplied asset manifest

All paths below are relative to this handoff package.

| Asset | Dimensions | Intended use | SHA-256 |
| --- | ---: | --- | --- |
| `assets/Capital Media Systems Corporate Mark.png` | 3548×1774 | Corporate masthead, company pages, footer | `979cc50ce50de5a7508d5784681ef96575f41e94b5656adcf7f5ea187585b99d` |
| `assets/ChannelDex Full Logo Color.png` | 2170×725 | Formal product introduction with CMS endorsement | `f3400bab0249adf2bd53d01f4f635f77491a04a2f0f59b09562722a4041d149a` |
| `assets/ChannelDex Full Logo Color with Tagline.png` | 4340×1450 | Introductory product application with descriptor | `e8c13ddd7193d8120b7d47b38e540b63aae6e13fbf2f2ba1fc9c792dcc232e` |
| `assets/ChannelDex Only Color.png` | 4340×1450 | Established product context | `324d202e1a0c10ae3dc297f7a38d9bd75a747a3cd92425be46f67af8aa4759ba` |
| `assets/ChannelDex Full Logo Grayscale.png` | 2170×725 | Reduced-color formal product use | `e715330f75d19506d184b92f89384c6b127bc3664c37601c384b7271fecbde11` |
| `assets/ChannelDex Full Logo Grayscale with Tagline.png` | 4340×1450 | Reduced-color descriptor use | `4311b1bbe4371428c4ecda4805b16dc11a2da5e636b0ee4284b4f7f56fcf32d9` |
| `assets/ChannelDex Only Grayscale.png` | 4340×1450 | Reduced-color established product context | `298e8a884db8a4e1cd26f7acb6ee28558d743767441add7fcd596441b9d8469f` |
| `assets/Full Logo Inverted.png` | 4340×1450 | Approved ChannelDex lockup on Charcoal | `2bb71133c00b7df7b93aa01b9d1ee349aeae4e322c77481a59b5b10f7cf5ce30` |
| `assets/App Icon.png` | 2048×2048 | ChannelDex application/launcher context only | `1803333caf8f1745959ae2e5c6510f401c187707f854328c63aa40ceee31022e` |
| `assets/Favicon.png` | 1824×1662 | ChannelDex browser-scale product identity | `ce73e2248fb90bdb679a23a660a35060c8302b4f8599188a7ece5efaa4497761` |

The PNGs have transparency. Preserve aspect ratios and required clear space. These are approved raster deployment assets, not substitutes for the source Illustrator masters when new vector exports are required.

### Font manifest

| Font | SHA-256 |
| --- | --- |
| `fonts/IBMPlexSans-Regular.ttf` | `8685116dfbcfa639621631c4af0ad62e52ddeb9979acbec0007ccef2e4801a27` |
| `fonts/IBMPlexSans-Medium.ttf` | `0ebb5ad4c9c57f2a2860f43ae9fa69024c045b380b2a73b332d6f40c39f4a294` |
| `fonts/IBMPlexSans-SemiBold.ttf` | `659f916ce1922bd4fecac7cd5f26018817a2946327a5dd00169c8bab14ae2c58` |
| `fonts/IBMPlexSans-Bold.ttf` | `cf74841e461235ac7205369329b14fd13a126af4c93927f8b287cad9aa6ffaab` |
| `fonts/IBMPlexMono-Regular.ttf` | `ab08018ccd276b79fb2c636bb95b9c543598f9d50505fe92506fcb4dae7810cd` |
| `fonts/IBMPlexMono-SemiBold.ttf` | `b1a936f96266fbe036b112f659155f7c32222af44a9a20f3bb011f51050669d5` |

IBM Plex licensing material should be carried into the final project from the original font archives or official distribution before public deployment.

## 20. Logo-use requirements

- Use the simplest complete supplied mark appropriate to the context.
- Maintain at least 1X clear space around the standalone ChannelDex symbol and app icon, where X is the height of one colored record.
- Maintain at least 1.5X around complete ChannelDex lockups.
- More clear space is preferred.
- Do not place text, borders, controls, photographs, or competing marks inside clear space.
- The official rule within the artwork is part of the logo and is exempt.
- Do not stretch, compress, rotate, mirror, skew, recolor, crop, outline, shadow, bevel, or glow any mark.
- Do not rebuild the wordmark, endorsement, corporate rule, tagline, or descriptor with HTML text.
- Do not use CSS filters to manufacture a reversed or monochrome mark.
- Do not move the endorsement or descriptor.
- Do not use the ChannelDex favicon as the corporate-site favicon unless the current page or subsite is specifically ChannelDex.
- A dedicated CMS favicon is not supplied and remains open. Use a conservative temporary text-free fallback only after approval.

Initial minimums from the current draft guide:

| Asset | Recommended digital minimum |
| --- | ---: |
| ChannelDex favicon | 16 px |
| Standalone ChannelDex symbol | 24 px high |
| ChannelDex-only lockup | 140 px wide |
| Full ChannelDex/CMS lockup | 220 px wide |
| Extended descriptor lockup | 300 px wide |
| Capital Media Systems corporate mark | 220 px wide |

## 21. Component specification

Build reusable, framework-agnostic components or partials for:

- Skip link
- Global header
- Desktop and mobile navigation
- Corporate mark
- Product lockup
- Hero
- Eyebrow/record label
- Button and text link
- Editorial split section
- Product feature panel
- Capability list
- System/archive card
- Timeline
- Principle card
- Image placeholder
- Contact details
- Footer
- Breadcrumbs on interior pages
- Accessible disclosure only if genuinely needed

Component rules:

- Components use semantic elements before ARIA.
- Links navigate; buttons perform actions.
- Focus is always visible.
- Hover is never the only way to reveal essential information.
- Cards do not become nested collections of conflicting links.
- Technical labels are decorative structure only when the same information is available in ordinary language.

## 22. Motion specification

Motion is restrained and functional:

- Header/nav transition: 150-220 ms
- Hover/focus color and rule transitions: 120-180 ms
- Optional section entrance: opacity plus no more than 8 px translation, once, 240-360 ms
- No parallax
- No auto-playing carousel
- No looping decorative animation
- No animated scan, interference, typing, blinking cursor, or glitch
- Honor `prefers-reduced-motion: reduce` by removing nonessential animation

The site must remain complete and visually balanced with motion disabled.

## 23. Responsive behavior

Design mobile-first with useful breakpoints derived from content rather than device models.

- Minimum supported content width: 320 px
- Body text never requires horizontal scrolling
- Logo assets retain aspect ratio and clear space
- Wide archive tables become stacked records or horizontally scroll within a labeled region
- Hero display type scales with `clamp()`
- Two- and three-column layouts collapse in a deliberate editorial order
- Navigation remains available without pointer hover
- Tap targets are at least 44×44 CSS pixels where practical

## 24. Accessibility contract

Target WCAG 2.2 AA:

- One logical `h1` per page and sequential headings
- Landmarks: header, nav, main, footer
- Skip-to-content link
- Full keyboard operation
- Visible focus with at least 3:1 non-text contrast
- At least 4.5:1 contrast for ordinary text and 3:1 for large text
- Non-color cues for every state
- Alternative text based on image purpose
- Form labels and errors if forms are later authorized
- Current-page indication in navigation
- Reduced-motion support
- No autoplaying audio or video
- Zoom to 200% without loss of content or function
- Reflow at 320 CSS px
- Automated accessibility checks plus manual keyboard review

## 25. Technical implementation contract

### Recommended shape

Create a separate, dependency-light static codebase. The final output must be ordinary HTML, CSS, JavaScript, fonts, and images that can be served by any standard web server.

Recommended file structure:

```text
capital-media-systems/
├── README.md
├── index.html
├── 404.html
├── channeldex/
│   └── index.html
├── systems/
│   └── index.html
├── company/
│   └── index.html
├── contact/
│   └── index.html
├── privacy/
│   └── index.html
├── accessibility/
│   └── index.html
├── assets/
│   ├── brand/
│   ├── fonts/
│   ├── images/
│   ├── css/
│   │   └── site.css
│   └── js/
│       └── site.js
├── robots.txt
├── sitemap.xml
├── Caddyfile.example
├── nginx.conf.example
└── tests/
    └── verify-site.*
```

A static-site generator is acceptable only if:

- Its generated output is conventional static files.
- The repository contains no dependency on a hosted builder or proprietary runtime.
- Content remains easy to edit.
- The build is deterministic and documented.
- A prebuilt deployable output can be produced.

Do not tie the site to ChatGPT Sites, Vercel, Netlify, Cloudflare Pages, a proprietary CMS, or a hosted font service.

### JavaScript

Use JavaScript only for progressive enhancement, such as mobile navigation. Essential content and navigation must work without it.

No third-party scripts, analytics, tag managers, chat widgets, embedded social feeds, or external trackers are permitted in the initial build.

### CSS

- Use documented custom properties for canonical colors, spacing, type, widths, borders, and motion.
- Use modern layout primitives: Grid, Flexbox, `clamp()`, and logical properties.
- Avoid utility-class soup in authored markup.
- Avoid encoding text inside pseudo-elements when it affects meaning.
- Include print styles for company and product pages.

### Metadata

Every page includes:

- Unique title and description
- Canonical URL placeholder documented for replacement
- Open Graph title, description, type, and image placeholder
- Appropriate favicon behavior once a corporate favicon is approved
- Theme color using Charcoal or Capital Blue
- Semantic language declaration

Structured data may include `Organization` and `SoftwareApplication` only after all public facts, URL, contact details, and availability claims are confirmed.

## 26. Self-hosting contract

The finished site must:

- Run from any static web root
- Use relative or root-relative local asset URLs
- Make no required outbound network requests
- Work behind Caddy, nginx, Apache, or equivalent
- Include an example configuration with sensible caching and security headers
- Avoid hardcoded localhost URLs
- Document domain replacement and TLS termination
- Provide a cache-busting strategy for changed assets
- Serve font files with correct MIME types
- Use immutable caching only for fingerprinted assets
- Avoid claiming that application-level security headers replace correct server administration

Recommended production headers:

- `Content-Security-Policy` limited to self-hosted resources
- `Referrer-Policy: strict-origin-when-cross-origin`
- `X-Content-Type-Options: nosniff`
- `Permissions-Policy` disabling unused browser capabilities
- Frame protection through CSP `frame-ancestors`
- HSTS only after HTTPS is correctly deployed

## 27. Performance contract

Targets for a representative mobile connection:

- No external font or JavaScript requests
- Initial JavaScript under 20 KB compressed unless justified
- Main stylesheet under 50 KB compressed unless justified
- Logo and hero media sized for rendered use
- Lazy-load below-the-fold photography
- Do not lazy-load the primary logo or Largest Contentful Paint image
- Explicit image dimensions
- Avoid layout shifts from fonts and media
- Lighthouse targets: Performance 90+, Accessibility 95+, Best Practices 95+, SEO 95+

Do not degrade legibility or image integrity merely to reach a numeric score.

## 28. SEO and content integrity

- Use plain, descriptive URLs.
- Keep one clear page topic per route.
- Do not add keyword-stuffed location or industry pages.
- Do not invent customer counts, years of service, awards, certifications, or testimonials.
- Do not list unsupported vendor compatibility.
- Do not claim that a planned or uploaded broadcast actually aired.
- Do not describe ChannelDex as controlling playout, fetching media, transcoding files, monitoring devices, or automatically assigning programming.
- Do not publish fictional archive products or chronology until approved.

## 29. Test and review plan

Before handoff, verify:

1. Every internal link resolves.
2. Every image and font resolves locally.
3. No request is made to a third-party origin.
4. Every page has a unique title, description, `h1`, and canonical placeholder.
5. All supplied marks retain aspect ratio and clear space.
6. Capital Blue and Station Blue are used in their correct identity contexts.
7. Keyboard navigation works at desktop and mobile widths.
8. Focus is visible.
9. Reduced-motion mode removes nonessential motion.
10. Pages reflow at 320 px and zoom to 200%.
11. Automated accessibility review reports no serious or critical issues.
12. Manual contrast checks pass for real text/background combinations.
13. The 404 page returns visitors to useful routes.
14. The static build runs under a simple local HTTP server.
15. Caddy/nginx examples serve correct MIME types and do not expose directory listings.
16. No proposed history, contact data, or commercial claim has lost its approval label during review.

## 30. Definition of done

The website is complete when:

- All specified routes exist and share a coherent responsive system.
- Current creative-folder assets are used correctly.
- The public copy is approved or visibly uses safe placeholders.
- ChannelDex is the clear flagship without consuming the corporate identity.
- The company feels established without fake aging.
- The faintly unsettling undertone remains optional to the reader and never obstructs credibility.
- The site is accessible, fast, and functional without third-party services.
- The deployable output is ordinary static files.
- Local preview and self-hosting instructions are documented.
- Tests and manual QA pass.

## 31. Decisions required before implementation

The owner should explicitly confirm or revise:

1. Approve the 1986 founding year.
2. Approve the proposed company history.
3. Approve the former products FrameLine, Archive/6, and RelayWorks, including dates and descriptions.
4. Approve IBM Plex as the website support family.
5. Confirm ChannelDex availability language: commercial, pilot, private deployment, or contact-only.
6. Supply or intentionally defer real company contact details.
7. Confirm whether the initial build uses only designed image placeholders.
8. Approve the dependency-free static architecture.
9. Approve the precise level of narrative unease represented here.

Until these are confirmed, implementation may build structure and clearly labeled placeholder content only if the governing project permits it. It must not publish proposed corporate history as fact.

## 32. Paste-ready instruction for another project

Use the following prompt with this handoff package attached:

> Build the Capital Media Systems corporate website described in `CAPITAL_MEDIA_SYSTEMS_WEBSITE_HANDOFF.md`. Treat the packaged `assets/` and `fonts/` directories as the only authoritative creative inputs. Preserve all accepted/proposed/open distinctions. Do not consult or reconstruct older ChannelDex artwork. Produce a self-hostable static codebase with no required cloud, CDN, hosted font, analytics, or ChatGPT Sites dependency. Follow the content deck, brand roles, accessibility contract, test plan, and definition of done exactly. Before implementation, present any remaining decisions that materially affect public factual claims and obtain approval where required by the project.

