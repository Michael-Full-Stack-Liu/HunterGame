# LinkedIn Patterns

Use these selectors as heuristics, not guarantees. Prefer trying multiple selectors in order.

## Common targets

- Connect button:
  - `button:has-text("Connect")`
  - `[aria-label*="Invite" i]`
- Add note:
  - `button:has-text("Add a note")`
- Note box:
  - `textarea[name="message"]`
  - `textarea`
- Send invite:
  - `button:has-text("Send")`
- Message button:
  - `button:has-text("Message")`
- Message composer:
  - `div[contenteditable="true"]`
  - `textarea`

## Safer process

1. Open the exact profile URL.
2. Confirm profile loaded via `/text` or `/eval`.
3. Look for visible connection indicators before acting.
4. If not connected, stop after sending the connection note.
5. Only after connection is confirmed should a referral ask or longer follow-up be sent.

## Good extraction targets

- Headline:
  - `document.querySelector("h1")?.innerText`
- Top card text:
  - `document.querySelector("main")?.innerText?.slice(0, 4000)`
- Connection-state hints:
  - buttons or badges containing `Connect`, `Pending`, or `Message`
