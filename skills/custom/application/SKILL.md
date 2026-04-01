---
name: application
description: Use before official website application to decide apply, referral-first, or skip.
---

# Application Skill

Use this skill when the workflow reaches the official company careers page or job application form.

## Core Principle

Do not apply just because a job exists.

The application workflow should answer:

1. Is this role actually a fit?
2. What is the strongest positioning angle?
3. Should the user apply now, pursue referral first, or skip?
4. If applying, what exact materials and next steps are needed?

## Apply Decision Framework

### Apply Now

Use when:

- the role strongly matches the candidate's core positioning
- the role is live and official
- enough research has been done to justify the application
- no critical mismatch is obvious

### Referral First, Then Apply

Use when:

- the role is high-value
- a warm path could materially improve the odds
- the company is selective or relationship-driven
- a relevant employee / manager is identifiable

### Skip

Use when:

- the role is weakly matched
- the work is too far from production AI / MLOps / infrastructure
- the location, seniority, or function is clearly off
- the posting appears stale, low-signal, or suspicious

## Evaluation Criteria

- Role fit
- Seniority fit
- Technical fit
- Evidence that the user's projects align with the work
- Whether the user has a credible hook for outreach or referral
- Whether official application steps are clear

## Output Format

When using this skill, structure your reasoning as:

### Application Decision
- Decision: Apply now / Referral first / Skip
- Confidence: High / Medium / Low

### Why
- 2 to 5 concise reasons

### Best Positioning Angle
- Which project / proof-point should lead the application

### Next Step
- Exact next action, such as:
  - submit through official website
  - ask {Name} for referral first
  - tailor resume for {specific requirement}
  - fill application form

## Official Website Rule

Prefer the official company careers site, Greenhouse, Lever, Ashby, Workday, or another clearly official ATS page.

Do not treat random reposted listings as the final application target if an official listing is available.
Do not recommend apply-now when there is no verified live posting or official application URL in the current run.

## Before `fill_job_form`

Confirm:

- This is the official application page
- The role is worth applying to
- The candidate profile is loaded
- The strongest positioning angle is known

## Final Check

- Did you make a real apply/no-apply decision?
- Did you identify the strongest project or proof-point?
- Did you avoid defaulting to "apply" without judgment?
