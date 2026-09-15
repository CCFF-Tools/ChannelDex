# Capital Media Systems website project kit

This folder is a portable planning and creative package for building the Capital Media Systems corporate website in a separate project.

It deliberately contains no website implementation. The governing project requires approval of the complete plan, public claims, scope, and open decisions before implementation begins.

## Start here

1. Read `AGENTS.md` for project rules.
2. Review `docs/DECISIONS.md` and resolve the approval gate.
3. Use `docs/WEBSITE_HANDOFF.md` as the comprehensive build and copy specification.
4. Use only the masters under `creative/` for identity work.
5. Consult `reference/channeldex/` for accepted product facts and operational boundaries.

## Directory map

```text
.
├── AGENTS.md
├── README.md
├── creative/
│   ├── Brand Guide/dist/
│   └── Finished Creative Assets/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ASSET_MANIFEST.md
│   ├── BRAND_GUIDELINES.md
│   ├── CONTENT_MODEL.md
│   ├── COPY_DECK.md
│   ├── DATA_MODEL.md
│   ├── DECISIONS.md
│   ├── DEVELOPMENT.md
│   ├── PRODUCT_PLAN.md
│   ├── TODO.md
│   └── WEBSITE_HANDOFF.md
└── reference/
    └── channeldex/
        ├── AGENTS.md
        ├── README.md
        ├── README_RUNTIME.md
        └── docs/
```

## Source status

- `creative/` is a clean copy of the current creative folder. Temporary renders and older `output/` brand exports are intentionally absent.
- `docs/` describes the proposed corporate website.
- `reference/channeldex/` preserves the source product documentation without pretending that it is the corporate website's own architecture.
- Statements must retain their **accepted**, **proposed**, or **open** status until the owner changes that status.

## Copying into another project

Copy this entire directory into a new folder, initialize version control there if desired, review the approval gate, and then implement the site alongside these planning materials. Do not flatten the directory or mix the ChannelDex reference snapshot with website-specific documents.
