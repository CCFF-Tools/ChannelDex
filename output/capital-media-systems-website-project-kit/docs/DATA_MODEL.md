# Capital Media Systems website data model

Status: **Proposed**

The corporate website does not require a database. Its “data model” is the structured editorial content described in `CONTENT_MODEL.md`: pages, sections, products, timeline entries, people, contact methods, media assets, claims, and placeholders.

If a static-site generator is selected, encode those records in local Markdown, JSON, YAML, or typed source modules. If the site is authored directly in HTML, maintain the same conceptual fields and truth statuses in the editorial claim register.

Do not introduce a CMS schema, persistence layer, API, or user model unless the owner separately approves that expansion.

## Required relationships

- A page contains ordered content sections.
- A section may reference one media asset and one call to action.
- A product may reference one approved identity master and multiple capability statements.
- A timeline entry may reference a product.
- Every potentially misleading public claim has a source and approval status.
- Every placeholder records the real asset it must eventually receive.
- Every public contact method has a verification date and approval status.

## Validation rules

- Page paths are unique.
- Every page has one title, meta description, and `h1`.
- Product names use approved capitalization.
- Proposed history remains visibly proposed until approved.
- Open contact data never becomes plausible-looking fake data.
- Media references resolve locally.
- Alternative text describes purpose rather than appearance alone.
- Archived products cannot be published until their records are accepted.

