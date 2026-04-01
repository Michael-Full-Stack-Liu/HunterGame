# Application Site Patterns

Use these patterns for Greenhouse, Lever, Workday-like, and custom pages.

## Common fill selectors

- First name:
  - `input[name*="first" i]`
- Last name:
  - `input[name*="last" i]`
- Email:
  - `input[type="email"]`
  - `input[name*="email" i]`
- Phone:
  - `input[type="tel"]`
  - `input[name*="phone" i]`
- LinkedIn:
  - `input[name*="linkedin" i]`
- Resume upload:
  - `input[type="file"][name*="resume" i]`
  - `input[type="file"]`
- Cover letter upload:
  - `input[type="file"][name*="cover" i]`

## Safe execution pattern

1. Open the official application URL.
2. Fill only fields you can identify confidently.
3. Upload resume and cover letter.
4. Capture a screenshot.
5. Avoid final submission unless policy explicitly allows it.

## When to prefer this skill over simple Playwright helpers

- The page requires an already logged-in browser session
- The page is very dynamic and standard selectors need iterative inspection
- The workflow needs a human Chrome profile with existing cookies
