# Capital Media Systems website development guide

Status: **Proposed**

Implementation is not authorized until the approval gate in `DECISIONS.md` is closed.

## Preparation

1. Read the root `AGENTS.md`.
2. Review `DECISIONS.md` and record owner approval.
3. Read `WEBSITE_HANDOFF.md` completely.
4. Open `../creative/Brand Guide/dist/index.html` in a browser.
5. Inspect the supplied masters under `../creative/Finished Creative Assets/`.
6. Review ChannelDex product truth under `../reference/channeldex/`.

## Implementation workflow

1. Create a conventional static web root outside the planning and creative directories.
2. Establish canonical CSS tokens and local font faces.
3. Build semantic shared components and page shells.
4. Implement routes using approved copy and visibly safe placeholders.
5. Produce web derivatives without modifying masters.
6. Add progressive enhancement, metadata, sitemap, robots file, and 404 behavior.
7. Add structural tests and self-hosting examples.
8. Render and review representative mobile, tablet, laptop, and wide layouts.
9. Run accessibility, link, asset, metadata, and third-party-request checks.
10. Audit every public claim against the decision record.

## Local preview

For a dependency-free static implementation, serve the web root rather than opening files directly:

```sh
python3 -m http.server 8080 --directory site
```

Then visit `http://127.0.0.1:8080/`.

If a generator is approved, document its exact install, build, test, and preview commands here. Do not require a hosted preview service.

## Asset handling

- Never overwrite a file in `creative/`.
- Keep source and generated assets separate.
- Preserve master aspect ratios and transparency.
- Record the master used for each derivative.
- Do not recolor identity artwork with CSS filters.
- Carry font license files into the implementation before public release.

## Code quality

- Prefer semantic HTML and understandable CSS over abstraction for its own sake.
- Keep JavaScript small, dependency-free where practical, and progressively enhanced.
- Do not add frameworks or packages that do not materially improve maintainability.
- Avoid inline style proliferation and duplicated page chrome.
- Ensure the site remains readable when styles or scripts fail.

## Review commands

The implementation must define the shortest commands for:

- complete build
- full affected test set
- internal-link and asset validation
- HTML validation
- accessibility automation
- local production-like preview

Record exact commands after the implementation stack is approved.

