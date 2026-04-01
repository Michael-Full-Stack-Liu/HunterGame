# Job Hunter Harness

Job Hunter Harness is a long-running job-search agent framework built for execution, not just chat.

It combines an LLM agent runtime, Telegram control, scheduled background cycles, SQLite state, web research, browser automation, email drafting, LinkedIn actions, company-site application prefills, and lightweight auditing into one system. The goal is to turn job hunting into a persistent semi-automated workflow that can keep researching, tracking, and acting across multiple cycles.

## What It Does

- Continuously research high-fit roles and companies
- Track target companies and next actions over time
- Search for referral paths on LinkedIn
- Draft outreach emails and follow-ups
- Prefill official application forms and hold final submission for approval
- Run scheduled background maintenance cycles
- Record traces, logs, and health signals for debugging and improvement

This is not a generic assistant. It is an execution-oriented harness for a real job-search pipeline.

## Core Architecture

The project is organized around a few main parts:

- `harness_engine/main.py`
  Starts the runtime, Telegram channel, scheduler, database setup, and dashboard/logging.

- `harness_engine/core/agent.py`
  Wraps the main LLM agent, injects profile/memory/skill context, and streams tool-driven execution.

- `harness_engine/core/scheduler.py`
  Runs autonomous cycles, follow-up checks, summaries, and audit triggers.

- `harness_engine/tools/builtins.py`
  Provides grounded search, page fetch, audit, and company-contact discovery tools.

- `harness_engine/tools/actuators.py`
  Provides execution tools such as email drafts, LinkedIn actions, browser automation, and application prefills.

- `harness_engine/channels/telegram.py`
  Exposes the agent through Telegram and supports status, summary, approval, and manual trigger commands.

## Background Cycle Design

Each autonomous cycle is designed to do more than produce a research summary.

The intended flow is:

1. Review existing memory, tracked companies, and recent progress
2. Research current opportunities or expand to new target companies
3. Execute one concrete low-risk action when possible
4. Summarize what changed and record next steps

If a cycle only researches and does not execute an action, the scheduler can trigger an additional forced execution pass.

## Current Execution Capabilities

- Grounded web research through Gemini-backed search
- Full-page fetch for official pages
- LinkedIn people discovery and connection flow checks
- Safe handling for LinkedIn auth walls and bot-detection challenges
- Official company contact discovery from public pages
- Email draft creation through IMAP drafts
- Official application prefill with screenshot capture
- Approval-gated final application submission

## Safety Model

This repo is built around a few practical safeguards:

- Low-risk actions can be automated
- Irreversible actions should stay approval-gated
- LinkedIn automation stops when login walls or bot-detection challenges are detected
- When LinkedIn is blocked, the system falls back to official-page contact discovery and email drafts
- Runtime data, personal materials, secrets, logs, and screenshots are intended to stay local and are ignored by `.gitignore`

## Repo Layout

Key paths:

- `harness_engine/`
- `skills/`
- `data_example/`
- `docs/`
- `docs_blueprint/`
- `job_hunter.sh`
- `job_hunter_doctor.py`
- `requirements.txt`

Local runtime data lives under `data/`, but most of it should not be committed.
Sanitized public examples live under `data_example/`.

## Quick Start

1. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Create your local configuration

- Add a local `.env`
- Create a local `config.yaml`
- Provide Telegram, model, email, and optional automation credentials
- Copy sample data shapes from `data_example/` into `data/` and replace the example values with your own

3. Start the system

```bash
./job_hunter.sh
```

The launcher will run `harness_engine/main.py` with the project virtualenv.

## Configuration Notes

The public repo is expected to omit local secrets and personal data.

Typical local configuration includes:

- model provider credentials
- Telegram bot token and chat id
- email account credentials for draft creation
- browser automation settings
- personal profile / resume paths

`config.yaml` is intentionally ignored for GitHub upload in the current setup.

## Operational Notes

- The system uses SQLite for runtime state
- Scheduler summaries and tool traces are written to `data/`
- The project currently tracks company progress and application snapshots, but the evaluation system is still early-stage
- Some workflows depend on logged-in browser sessions and external services

## Current Status

The infrastructure is already usable for long-running experimentation, but the evaluation loop is still evolving.

What is working well:

- long-running scheduled cycles
- tool-based execution flow
- tracked company progress
- safe fallback behavior when LinkedIn is blocked

What still needs iteration:

- stronger contact discovery beyond LinkedIn internal search
- richer event-level evaluation and analytics
- more robust multi-company pipeline comparisons

## Notes For GitHub Upload

Before pushing this repo publicly, double-check that you are not committing:

- `.env`
- `config.yaml`
- local database files
- logs and traces
- screenshots
- personal profile/resume materials
- private generated job-search artifacts

The current `.gitignore` is set up to exclude those local files by default.
