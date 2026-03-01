# Note Expander

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-compose-blue.svg)](docker-compose.yml)

A Discord bot + local web dashboard that receives brief ideas or voice memos and runs a 4-agent AI pipeline to research them, producing polished markdown reports. Runs entirely on your local machine with Ollama — no cloud LLM fees required.

---

## What it does

Send a short note like `"quantum error correction"` or attach a voice memo, and Note Expander:

1. **Classifies** the input (topic type, confidence, required depth)
2. **Researches** it via Tavily web search (optional) or URL fetching
3. **Analyses** the findings into structured insights
4. **Formats** a polished markdown report
5. **Delivers** the report back via Discord embed + saves it to the local dashboard

**Features:**
- Voice memo transcription via Whisper (offline)
- URL research mode — paste a link and it fetches + summarises the page
- Live dashboard at `http://localhost:8765` with note history, analytics, and version tracking
- Re-run notes, compare two notes side-by-side, or ask follow-up questions via slash commands
- Obsidian vault sync and webhook notifications for completions
- Tags to control pipeline behaviour: `#deep`, `#quick`, `#market`, `#tech`, `#noformat`
- Analytics page showing activity over time and top search queries

---

## Architecture

```
Discord Message / Voice Memo
        │
        ▼
┌───────────────────┐
│  Discord Bot      │  /research, /status, /compare, /followup
│  (discord.py)     │  + voice → Whisper transcription
└────────┬──────────┘
         │ async task
         ▼
┌─────────────────────────────────────────┐
│  4-Agent CrewAI Pipeline                │
│                                         │
│  1. Classifier  → topic type, flags     │
│  2. Researcher  → Tavily / URL fetch    │
│  3. Analyst     → structured insights  │
│  4. Formatter   → markdown report      │
└────────┬────────────────────────────────┘
         │
         ▼
┌───────────────────┐        ┌──────────────────────┐
│  SQLite (async)   │◄──────►│  FastAPI Dashboard   │
│  notes + reports  │        │  HTMX live updates   │
└───────────────────┘        └──────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│  Integrations                    │
│  • Discord rich embed on finish  │
│  • Obsidian vault sync (.md)     │
│  • Webhook POST (Zapier, n8n…)   │
└──────────────────────────────────┘
```

---

## Prerequisites

**For the Docker path (recommended):**
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) or Docker + Docker Compose v2
- A Discord bot token (see below)
- Optional: [Tavily API key](https://tavily.com/) for web search (free tier available)

**For the native path:**
- Python 3.11+
- `ffmpeg` (`brew install ffmpeg` / `apt install ffmpeg`)
- [Ollama](https://ollama.com/) running locally

### Creating a Discord Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and click **New Application**
2. Under **Bot**, click **Add Bot** and copy the token — this is your `DISCORD_TOKEN`
3. Under **Bot → Privileged Gateway Intents**, enable **Message Content Intent**
4. Under **OAuth2 → URL Generator**, select scopes: `bot`, `applications.commands`
   - Bot permissions: `Send Messages`, `Read Message History`, `Attach Files`, `Use Slash Commands`
5. Copy the generated URL and open it in a browser to invite the bot to your server

---

## Quick Start (Docker)

```bash
# 1. Clone
git clone https://github.com/your-username/noteexpander.git
cd noteexpander

# 2. Configure secrets
cp .env.example .env
# Edit .env — set DISCORD_TOKEN (required) and TAVILY_API_KEY (optional)

# 3. Configure Ollama host for Docker networking
# In config.yaml, change:
#   ollama_base_url: "http://localhost:11434"
# to:
#   ollama_base_url: "http://ollama:11434"

# 4. Start the stack
docker compose up -d

# 5. Pull an Ollama model (first run only — ~5 GB download)
docker exec noteexpander-ollama ollama pull qwen3:8b

# 6. Open the dashboard
open http://localhost:8765
```

Invite your bot to a Discord server, then try: `/research quantum error correction`

**GPU acceleration** (Nvidia): uncomment the `deploy` section in `docker-compose.yml` under the `ollama` service.

---

## Native Install

```bash
# Prerequisites: Python 3.11+, ffmpeg, Ollama running at localhost:11434

git clone https://github.com/your-username/noteexpander.git
cd noteexpander

# Install dependencies
pip install -r requirements.txt
# Or with dev tools:
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env — set DISCORD_TOKEN and optionally TAVILY_API_KEY

# Pull a model via Ollama
ollama pull qwen3:8b

# Run
python main.py
```

---

## Configuration

All non-secret settings live in `config.yaml`. Secrets go in `.env`.

### config.yaml reference

| Key | Default | Description |
|-----|---------|-------------|
| `llm.provider` | `"ollama"` | `"ollama"` or `"openai"` |
| `llm.ollama_base_url` | `"http://localhost:11434"` | Ollama server URL. Use `"http://ollama:11434"` in Docker |
| `llm.ollama_model` | `"qwen3:8b"` | Model to pull and use with Ollama |
| `llm.openai_model` | `"gpt-4o-mini"` | OpenAI model (when `provider = "openai"`) |
| `llm.temperature` | `0.3` | Generation temperature |
| `llm.max_tokens` | `4096` | Max tokens per LLM call |
| `llm.timeout_seconds` | `120` | Per-call timeout (increase for slow hardware) |
| `llm.max_retries` | `2` | Retry count on timeout or empty response |
| `whisper.model` | `"base"` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large` |
| `whisper.language` | `"en"` | Transcription language |
| `dashboard.host` | `"0.0.0.0"` | Dashboard bind address (`0.0.0.0` = LAN only) |
| `dashboard.port` | `8765` | Dashboard port |
| `discord.allowed_user_ids` | `[]` | Restrict bot to these user IDs (empty = all users) |
| `discord.notification_channel_id` | `null` | Send completions here instead of the originating channel |
| `search.enabled` | `true` | Enable Tavily web search in the pipeline |
| `search.max_results` | `5` | Max search results to pass to the analyst |
| `processing.max_concurrent_jobs` | `2` | Parallel pipeline limit |
| `processing.db_path` | `"data/noteexpander.db"` | SQLite database path |
| `integrations.obsidian_vault_path` | `null` | Path to Obsidian vault folder for auto-sync |
| `integrations.webhook_url` | `null` | Webhook URL for completion notifications |

### .env reference

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Bot token from the Discord developer portal |
| `TAVILY_API_KEY` | No | Enables web search ([get a free key](https://tavily.com/)) |
| `OPENAI_API_KEY` | No | Required only when `llm.provider = "openai"` |
| `DISCORD_GUILD_ID` | No | Restrict slash command registration to one server (faster) |

---

## Pipeline Overview

The pipeline is built with [CrewAI](https://github.com/crewAIInc/crewAI) and runs four agents in sequence:

1. **Classifier** — determines topic type (`tech`, `market`, `science`, `personal`, etc.), confidence level, and whether deep research is warranted
2. **Researcher** — executes Tavily searches or fetches a provided URL; skipped for `#quick` notes
3. **Analyst** — synthesises raw search results into structured findings with key themes and evidence
4. **Formatter** — produces the final polished markdown report; skipped for `#noformat` notes

Agent prompts are fully customisable via `prompts.yaml`.

---

## Available Tags

Add tags anywhere in your note to modify pipeline behaviour:

| Tag | Effect |
|-----|--------|
| `#deep` | Use OpenAI instead of Ollama for this note (requires `OPENAI_API_KEY`) |
| `#quick` | Skip the Researcher stage entirely |
| `#market` | Force classification as a market/business topic |
| `#tech` | Force classification as a technology topic |
| `#noformat` | Skip the Formatter stage; returns raw analysis |

---

## Slash Commands

| Command | Description |
|---------|-------------|
| `/research <note>` | Submit a text note for research |
| `/status <id>` | Check the status of a note by ID |
| `/compare <id1> <id2>` | Compare two notes side-by-side |
| `/followup <id> <question>` | Ask a follow-up question about a completed note |
| `/listen` | Start auto-processing messages in this channel |
| `/unlisten` | Stop auto-processing in this channel |
| `/help` | Show command reference |

---

## Integrations

### Obsidian Vault Sync

Set `integrations.obsidian_vault_path` in `config.yaml` to a folder inside your Obsidian vault. Completed reports are written as `.md` files automatically.

```yaml
integrations:
  obsidian_vault_path: "~/Documents/MyVault/Note Expander"
```

### Webhooks

Set `integrations.webhook_url` to receive a JSON POST on every completed pipeline. Compatible with Zapier, n8n, Make, and any HTTP endpoint.

```yaml
integrations:
  webhook_url: "https://hooks.zapier.com/hooks/catch/..."
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup, code style guidelines, and PR process.

---

## License

[MIT](LICENSE)
