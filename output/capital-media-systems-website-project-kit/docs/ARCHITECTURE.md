# Capital Media Systems website architecture

Status: **Proposed**

## Architecture decision

Build a dependency-light static website whose deployable result is ordinary HTML, CSS, JavaScript, images, and fonts.

The implementation may use no generator at all or a small static-site generator, provided that:

- generated output is conventional static files;
- content remains easy to edit;
- builds are deterministic and documented;
- a hosted build service is not required;
- prebuilt output can be deployed to any normal web server.

## Runtime boundary

The public site has no application runtime. JavaScript is limited to progressive enhancement such as mobile navigation. Essential content and navigation must remain available without scripting.

## Proposed implementation tree

```text
site/
├── index.html
├── 404.html
├── channeldex/index.html
├── systems/index.html
├── company/index.html
├── contact/index.html
├── privacy/index.html
├── accessibility/index.html
├── assets/
│   ├── brand/
│   ├── fonts/
│   ├── images/
│   ├── css/site.css
│   └── js/site.js
├── robots.txt
└── sitemap.xml
```

Planning, creative masters, tests, and deployment examples stay outside the deployed web root.

## Component model

- Skip link
- Global header and navigation
- Corporate and product identity blocks
- Hero
- Editorial split section
- Eyebrow/technical label
- Buttons and text links
- Product feature panel
- Capability list
- Systems archive card
- Timeline
- Principle card
- Art-directed image placeholder
- Contact details
- Breadcrumbs
- Footer

## Asset pipeline

Do not edit files under `creative/`.

1. Copy the required master into an implementation source-assets folder.
2. Produce appropriately sized web derivatives.
3. Preserve transparency and aspect ratio.
4. Record derivative dimensions and source filename.
5. Fingerprint deployable assets if long-lived immutable caching is used.
6. Keep identity masters lossless; use WebP/AVIF primarily for photography.

## CSS architecture

Use a small layered stylesheet:

1. Font faces and tokens
2. Reset/base elements
3. Typography
4. Layout primitives
5. Components
6. Page compositions
7. Utilities limited to accessibility and unavoidable state
8. Responsive and print rules

Canonical design tokens must include identity colors, type families, type scale, spacing, widths, rules, radii, shadows, and motion.

## Accessibility

Target WCAG 2.2 AA. Use semantic HTML before ARIA, one logical page heading, keyboard-accessible navigation, visible focus, non-color state cues, useful alternative text, reduced-motion behavior, 320 px reflow, and 200% zoom support.

## Privacy and security

The initial site has no analytics, advertising, tracking pixels, third-party scripts, accounts, or data-collecting forms. Production server logs are a hosting concern and must not be denied in public copy unless verified.

Example hosting configuration should document a self-only Content Security Policy, strict referrer behavior, content-type protection, a restrictive permissions policy, and frame ancestors. HSTS is enabled only after HTTPS is correctly deployed.

## Deployment

The site must run from Caddy, nginx, Apache, or equivalent static hosting. No hardcoded local URLs or platform-specific routing assumptions are permitted. Include local preview instructions and example Caddy/nginx configurations.

## Performance targets

- No required third-party requests
- JavaScript under 20 KB compressed unless justified
- Main CSS under 50 KB compressed unless justified
- Explicit image dimensions
- Lazy loading below the fold
- No lazy loading for the LCP image
- Lighthouse targets: Performance 90+, Accessibility 95+, Best Practices 95+, SEO 95+

## Verification

Automate internal-link, missing-asset, essential-metadata, single-`h1`, and outbound-origin checks. Manually review keyboard operation, reduced motion, responsive layout, contrast, image treatment, identity clear space, and public factual claims.

