---
name: referral
description: Use for LinkedIn referral strategy, connection notes, warm outreach, and referral requests.
---

# Referral Skill

Use this skill when the goal is not broad networking, but specifically to create a **warm path** into a company through LinkedIn.

## When To Use

Use this skill when:

- a strong-fit role has already been identified, or there is a clearly labeled target-company hypothesis
- the company is valuable enough that a warm introduction could materially improve odds
- the agent needs to choose *who* to contact on LinkedIn
- the agent needs to write:
  - a connection request note
  - a post-connection follow-up
  - a referral request
  - a light-touch warm intro message

Do **not** use this skill for:

- cold email to public company inboxes
- formal recruiter email replies
- generic networking with no target role
- official application-page decisions

For those cases, use `outreach` or `application`.

## Core Objective

The purpose of referral outreach is to get one of these outcomes:

1. connection accepted
2. short reply with guidance
3. confirmation of team fit
4. referral offer
5. recommendation on the best application path

The goal is **not** to impress with a long message.

## Referral Workflow

Always think in this sequence:

1. Confirm the role or company is worth pursuing.
2. Identify the best contact tier.
3. Check connection status first.
4. If not connected:
   - send only a connection request note
   - do not ask for referral yet
5. If already connected:
   - send a short warm message or direct referral request
6. Keep the ask small and credible.
7. If no response, prepare one concise follow-up only.

If the role is not verified live, say that clearly and treat the company as a target hypothesis, not a confirmed opening.

## Connection Rule

Referral outreach is a two-stage process:

- Stage 1: become a first-degree connection
- Stage 2: ask for guidance or referral

If the target is not already connected, do not jump directly to the referral ask.

Default state handling:

- if status is unknown, inspect first
- if not connected, send only a connection request note
- if pending, wait and do not send a referral ask
- if connected, send the short warm message or referral request

If execution tools are available, use this order:

1. `linkedin_search_people` if the target person is not yet known
2. `linkedin_connection_status`
3. `linkedin_connect_preview` when you need to confirm the connect UI is available without sending
4. `linkedin_referral_outreach` as the default execution path
5. `linkedin_connect` only for an explicit connection-only step
6. `linkedin_send_message` only after connection exists

If the environment is ready and the company or role target is already known, execute the referral workflow directly. Do not ask the user whether you should start LinkedIn search or whether you should use the current outreach logic.

For discovery outside LinkedIn:

- prefer grounded `web_search` and official-page `web_fetch`
- do not use browser automation to drive Google/Bing search result pages for routine profile discovery
- if LinkedIn is blocked or candidate discovery is empty, fall back to official company pages and email/contact discovery before more browser-based searching

## Contact Priority

Prefer contacts in this order:

1. Team member in the same function as the role
2. Hiring manager or engineering lead
3. Recruiter attached to the role
4. Founder / CTO only when the company is small or the role is highly strategic

Avoid messaging random unrelated employees just because they work at the company.
If profile discovery is available, prefer returning 2-5 likely targets with a brief reason for the top choice.

## Decision Rules

### Choose team member first when:

- the role is clearly technical
- the person likely understands the work
- you need fit validation or a practical referral path

### Choose hiring manager when:

- the company is small to medium
- the role appears strategic
- the manager is visible and likely close to hiring decisions

### Choose recruiter when:

- the posting looks process-heavy
- the recruiter is clearly attached to the role
- you mainly need routing, status, or process confirmation

### Choose founder / CTO only when:

- company is small
- technical leadership is accessible
- the candidate's project fit is unusually strong

## Message Types

### 1. Connection Request Note

Use when not connected yet. Keep it short enough for LinkedIn connection notes.

Template:

Hi {Name} — I’m exploring {role family} opportunities and {Company} stood out. I build production-grade AI / MLOps systems and thought it would be great to connect.

Alternative:

Hi {Name} — I’m interested in the {Role} opportunity at {Company}. My background is in production AI infrastructure and long-running automation systems. Would love to connect.

## 2. Post-Connection Follow-Up

Use after the connection is accepted.

Template:

Hi {Name}, thanks for connecting. I’m exploring {role family} opportunities and {Company} caught my attention because {specific signal}. My background is strongest in {relevant strength}, especially {project/proof}. If this looks relevant from your side, I’d really appreciate any advice on the best path in.

## 3. Direct Referral Request

Use only when the connection already exists, the role is clear, and the fit is credible.

Template:

Hi {Name}, I’m reaching out because I’m interested in the {Role} opening at {Company}. My strongest fit is in {relevant strength}, and one relevant example is {project/proof}. From your perspective, does this seem aligned with what the team needs, and if so, would you be open to referring me?

## 4. Recruiter-Oriented Warm Message

Template:

Hi {Name}, I’m interested in the {Role} position at {Company}. My background is strongest in production-grade AI systems, MLOps, and infrastructure-focused engineering. A relevant example is {project/proof}. If this looks aligned, I’d appreciate guidance on the best next step.

## Writing Rules

- Keep the first touch short.
- Use exactly one proof-point.
- Use exactly one ask.
- Use one company-specific signal when available.
- Prefer practical language over enthusiastic language.
- Sound like an engineer, not a marketer.

For unconnected targets:

- the connection note should not contain the referral ask
- the only goal is to earn the connection

## Tone Rules

- respectful
- low-pressure
- specific
- concise
- credible

Avoid:

- over-explaining
- exaggerated self-praise
- emotional pressure
- generic admiration
- asking for too much too early

## Anti-Patterns

Never write:

- "Can you refer me please?"
- "I know this is a big ask..."
- "I really need this opportunity"
- "I am the perfect fit"
- "Your company is very inspiring"

Never send a long essay as a connection request.

## Fit Anchors

Default candidate themes to reuse:

- production hardening
- MLOps and AI infrastructure
- long-running automation systems
- LLM / agent workflow engineering
- debugging difficult regressions
- reliability and self-healing pipelines
- end-to-end ownership

## Follow-Up Rule

If there is no reply:

- do not spam
- send at most one concise follow-up
- the follow-up should add one useful point or restate the ask more clearly
- if the connection request is still pending, do not send a separate message

Example:

Hi {Name}, following up on my earlier note in case this role is still active. My background in {relevant strength}, especially {project/proof}, seems relevant to the team’s work. If this looks aligned, I’d appreciate any guidance on the best path in.

## Output Expectations

When using this skill, the agent should usually produce:

1. recommended contact type
2. reason this contact type is best
3. connection-aware next action
4. the exact message draft
5. optional follow-up draft if relevant

When the tooling is available, prefer this stronger output:

1. target selection
2. actual `linkedin_search_people` result summary
3. actual execution result from `linkedin_connect` or `linkedin_send_message`
4. exact message text used
5. only the remaining blocker, if any

If direct profile discovery is not available, ask only for the minimum missing input needed to execute:

1. company name
2. target role or function
3. one LinkedIn profile URL or one target name

Do not ask the user to do broad manual research if a narrower next step is possible.
Do not ask the user for routine permission to begin a search or send a standard connection request when the workflow already called for referral execution.

## Final Check

Before finalizing any referral message, verify:

- Is the target the right person?
- Is the ask small and reasonable?
- Is there one clear candidate proof-point?
- Is the message short enough for the channel?
- Does it sound credible and low-pressure?
