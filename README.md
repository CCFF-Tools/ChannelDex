# PUB-TV

PUB-TV is a local-first scheduling and logging app for one owner/editor on one computer. The product plan, data model, architecture, decisions, and Python + Django + SQLite stack were approved for implementation on 2026-09-09. This repository now contains an implemented V1 scaffold; packaging and operational pilot work remain separate.

The accepted initial runtime, packaging, and testing target is macOS 26.x on Apple Silicon on the development machine. Intel Mac support is a later compatibility-validation target and is not yet proven; no single universal binary is promised. Packaging mechanics, installation restrictions, and the final distribution form remain open.

The first station boundary is PUB-TV. Scheduling, catalog, and broadcast history remain core; V1 manually plans the complete 24-hour day, including programs, filler, station IDs, PSAs, and live events, alongside playlist preparation and media-management notes. Reusable non-episode assets and timed live entries are first-class schedule items. Preserve confirmed history with source and actor provenance. Manual history entry comes first; Leightronix file/schedule exchange and broadcast control remain future roadmap items. The design should not require vendor APIs, external hosting, cloud or CDN runtime services, or an AI runtime.

Replays always use the same episode as that week's premiere. When there is no new premiere, staff choose an older episode case by case. Keep a complete planned day distinct from evidence that it actually aired. The scheduling week starts Monday at 00:00 Eastern on a macOS host. Private in-app day/week/agenda views are V1; export is a near-term roadmap follow-up. Public access and shared LAN/accounts are deferred.

Planning documents:

- [Product plan](docs/PRODUCT_PLAN.md)
- [Data model](docs/DATA_MODEL.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Decisions](docs/DECISIONS.md)

The implemented scaffold covers local Django/SQLite records, server-rendered planning views, recurrence and weekly assignment rules, preparation/history provenance, conflict warnings, and stale-edit checks. It does not claim macOS packaging, vendor exchange, device automation, public/LAN access, or production deployment. Proposed, accepted, and open statements remain clearly distinguished in project documentation.
