---
name: web-access-skill
description: Use when work requires logged-in or dynamic web pages, especially LinkedIn and official application sites. Pair Gemini grounding or Jina for discovery/reading with the bundled CDP proxy for real browser actions such as opening tabs, clicking, scrolling, extracting DOM text, and uploading resume files through the user's existing Chrome session.
triggers:
  - web access skill
  - logged in browser automation
  - linkedin browser automation
  - use chrome cdp
  - interact with dynamic pages
  - upload files in browser
  - automate linkedin in browser
  - automate official application page
---

# Web Access Skill

Use this skill for pages that need a real browser session:
- LinkedIn profile viewing, connect flows, and message drafting
- official application pages with uploads
- authenticated dashboards or heavy client-side apps

Do not use this skill as the primary search engine.
- Use `web_search` for discovery. In this repo it already prefers Gemini Google Search grounding.
- Use `web_fetch` for simple page reads. In this repo it already prefers Jina Reader.
- Use this skill when the page needs login, DOM interaction, or file upload.

## Quick Start

1. Verify prerequisites:
```bash
bash skills/public/web-access-skill/scripts/check-deps.sh
```

2. Start the local proxy:
```bash
venv/bin/python skills/public/web-access-skill/scripts/cdp_proxy.py
```

3. Confirm it is alive:
```bash
curl -s http://127.0.0.1:3456/ping
```

4. Use the HTTP endpoints below to drive the user's existing Chrome session.

If the project exposes a `browser_bootstrap` tool, prefer calling it before browser-heavy workflows so the remote-debug Chrome profile can be started automatically.

## Required Chrome Setup

Chrome must already be running with remote debugging enabled and with the user's desired login state.

Preferred launch pattern:
```bash
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.config/google-chrome"
```

If Chrome is already open normally, the user may instead enable remote debugging from:
`chrome://inspect/#remote-debugging`

## Core Endpoints

All responses are JSON.

### Health
```bash
curl -s http://127.0.0.1:3456/ping
```

### Open a new tab
```bash
curl -s "http://127.0.0.1:3456/new?url=https://www.linkedin.com/feed/"
```

### Close a tab
```bash
curl -s "http://127.0.0.1:3456/close?target=<target-id>"
```

### Evaluate JavaScript
```bash
curl -s -X POST "http://127.0.0.1:3456/eval?target=<target-id>" \
  --data 'document.title'
```

### Extract visible text
```bash
curl -s "http://127.0.0.1:3456/text?target=<target-id>"
```

### Click by selector
```bash
curl -s -X POST "http://127.0.0.1:3456/click?target=<target-id>" \
  --data 'button'
```

### Real pointer click
Use this for brittle widgets or buttons that ignore normal DOM clicks.
```bash
curl -s -X POST "http://127.0.0.1:3456/clickAt?target=<target-id>" \
  --data 'button'
```

### Fill a field
```bash
curl -s -X POST "http://127.0.0.1:3456/fill?target=<target-id>" \
  -H "Content-Type: application/json" \
  -d '{"selector":"textarea","value":"Hello from the browser skill"}'
```

### Upload files
```bash
curl -s -X POST "http://127.0.0.1:3456/setFiles?target=<target-id>" \
  -H "Content-Type: application/json" \
  -d '{"selector":"input[type=file]","files":["/abs/path/resume.pdf"]}'
```

### Scroll
```bash
curl -s "http://127.0.0.1:3456/scroll?target=<target-id>&direction=bottom"
```

### Screenshot
```bash
curl -s "http://127.0.0.1:3456/screenshot?target=<target-id>&file=/tmp/web-access-shot.png"
```

## Default Dispatch Rules

- `web_search`: discover live openings, company pages, public profiles, official apply URLs
- `web_fetch`: read a public page cheaply
- `web-access-skill`: operate inside a real logged-in tab or interact with a dynamic page

## LinkedIn Workflow

Use this skill when you already know the target profile URL or search URL and need a logged-in browser.

1. Open the LinkedIn page with `/new`
2. Wait briefly with `/eval` if the page is still rendering
3. Use `/text` or `/eval` to inspect profile content or connection state
4. If sending a connection request:
   - click `Connect`
   - optionally click `Add a note`
   - use `/fill` on the note textarea
   - click the final send button
5. If already connected:
   - open the message composer
   - use `/fill`
   - click send only when explicitly allowed by workflow

Connection-first rule:
- For referral flows, do not send a direct ask before verifying the target is already a first-degree connection.

## Official Application Workflow

Use this skill when the user has an official application URL and the page requires browser interaction or uploads.

1. Open the page with `/new`
2. Fill straightforward text fields with `/fill`
3. Upload resume and cover letter with `/setFiles`
4. Take a screenshot with `/screenshot`
5. Do not click the final submit button unless the surrounding automation policy explicitly allows submit

## References

- For LinkedIn-specific patterns, read [references/linkedin.md](references/linkedin.md)
- For official application-site patterns, read [references/application-sites.md](references/application-sites.md)
