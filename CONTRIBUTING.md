# Contributing to Note Expander

Thank you for your interest in contributing. This document covers how to set up a development environment, the code style we follow, and the process for submitting changes.

---

## Development Setup

### Prerequisites

- Python 3.11+
- `ffmpeg` (`brew install ffmpeg` / `apt install ffmpeg`)
- [Ollama](https://ollama.com/) running locally with at least one model pulled
- A Discord bot token (see [README.md — Creating a Discord Bot](README.md))

### Install

```bash
git clone https://github.com/your-username/noteexpander.git
cd noteexpander

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install with dev dependencies
pip install -e ".[dev]"

# Copy and fill in secrets
cp .env.example .env
```

### Running locally

```bash
python main.py
```

The dashboard will be at `http://localhost:8765`.

---

## Code Style

We use [ruff](https://github.com/astral-sh/ruff) for linting and formatting.

```bash
# Check
ruff check app/ main.py

# Fix auto-fixable issues
ruff check --fix app/ main.py
```

Line length: **100 characters**. Target: **Python 3.11+**.

### Type checking

```bash
mypy app/ main.py --ignore-missing-imports
```

We aim for typed function signatures on all public functions. Third-party libraries without stubs are excluded via `ignore_missing_imports = true`.

---

## Running Tests

```bash
pytest
```

Tests live in `tests/`. New features should include at minimum a smoke test.

---

## Project Structure

```
noteexpander/
├── app/
│   ├── agents/          # CrewAI pipeline (crew.py, definitions.py, tools.py)
│   ├── dashboard/       # FastAPI server + Jinja2 templates
│   ├── database/        # SQLAlchemy models, async session, CRUD helpers
│   ├── discord_bot/     # discord.py client, slash commands, Whisper, URL fetcher
│   ├── integrations/    # Obsidian sync, webhook notifications
│   └── pipeline/        # process_note() async orchestrator
├── config.yaml          # All non-secret settings
├── prompts.yaml         # Agent prompts (customise freely)
├── main.py              # Entry point
└── requirements.txt     # Runtime dependencies
```

### Key design decisions

- **Synchronous pipeline in a thread**: CrewAI's synchronous API is wrapped in `asyncio.to_thread()` in `pipeline/processor.py` to avoid blocking the Discord event loop.
- **SQLite only**: Zero-config persistence via `aiosqlite` + SQLAlchemy async. For production-scale deployments, the session layer can be swapped to PostgreSQL.
- **HTMX for the dashboard**: No JS framework. Status polling uses `hx-get` + `hx-trigger="every 5s"` on the note detail page.
- **Config over code**: All tuneable parameters (models, timeouts, paths) live in `config.yaml`. Agent prompts are in `prompts.yaml`. Nothing is hardcoded.

---

## Pull Request Process

1. Fork the repository and create a branch: `git checkout -b feature/my-feature`
2. Make your changes and add tests where appropriate
3. Run `ruff check` and `mypy` and fix any issues
4. Commit with a clear message describing *why* the change is needed
5. Open a pull request against `main` with a description of the change and how to test it

For significant changes (new pipeline stages, new integrations, schema migrations), open an issue first to discuss the approach.

---

## Reporting Bugs

Please open a GitHub issue with:
- A short description of the problem
- Steps to reproduce
- The relevant section of `logs/app.log` (redact your API keys)
- Your OS, Python version, and Ollama model

---

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
