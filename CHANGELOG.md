# Changelog

All notable changes to Note Expander are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/).

---

## [0.1.0] — 2026-03-01

### Added

**Core pipeline**
- 4-agent CrewAI pipeline: Classifier → Researcher → Analyst → Formatter
- Offline voice memo transcription via OpenAI Whisper
- Tavily web search integration (optional, free tier)
- URL research mode — paste any URL to fetch and summarise the page
- Per-note pipeline flags via tags: `#deep`, `#quick`, `#market`, `#tech`, `#noformat`

**Discord bot**
- `/research <note>` — submit a text note for research
- `/status <id>` — check note processing status
- `/compare <id1> <id2>` — side-by-side comparison of two notes
- `/followup <id> <question>` — ask a follow-up question about a completed note
- `/listen` / `/unlisten` — toggle auto-processing in a channel
- Rich Discord embeds on pipeline completion (type colour, confidence %, key themes)

**Local web dashboard**
- Note history with sorting (date / confidence / type) and pagination
- Live status polling via HTMX (no page reloads)
- Note detail view with report, sources tab, and version history
- Analytics page: notes per day, top search queries (CSS-only charts)
- Re-run notes (snapshots previous version before clearing)

**Integrations**
- Obsidian vault sync — completed reports written as `.md` files
- Webhook notifications — POST JSON payload on completion (Zapier, n8n, Make, …)

**Infrastructure**
- Docker Compose setup with Ollama + app services
- SQLite persistence via aiosqlite / SQLAlchemy async (zero config)
- Configurable via `config.yaml` and `prompts.yaml`
- GitHub Actions CI (ruff + mypy)

[0.1.0]: https://github.com/your-username/noteexpander/releases/tag/v0.1.0
