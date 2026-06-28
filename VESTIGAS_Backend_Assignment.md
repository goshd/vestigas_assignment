# VESTIGAS – Backend Engineering Assignment

## The context you're walking into

VESTIGAS digitizes the construction supply chain. We sit between suppliers, logistics partners, and construction sites, and we make their delivery data make sense to each other.

We've just onboarded two new logistics partners. The problem: each partner exposes their data differently, both APIs are sometimes flaky, and nothing they return is in a shape our systems can consume directly.

Your job is to build the backend service that pulls their data in, reconciles it, stores it, and makes it queryable by other teams at VESTIGAS.

This brief is intentionally not a specification.

---

## Who needs what

This is what we've heard from internal stakeholders. They don't agree on everything, and they haven't thought it all through.

- **Operations** wants one view across both partners. They don't care which partner a record came from; they care whether the delivery happened, who signed for it, when, and where.
- **Operations** wants to prioritize their day — there are too many deliveries to look at all of them. They've already settled on a scoring formula (see *Delivery score* below); your job is to compute it correctly and make it the default ordering wherever ops sees deliveries.
- **Construction Site managers** want to ask: "what came in to my site, on this date?" That query needs to work across both partners as one.
- **The fetch is on-demand for now.** Ops wants to trigger a refresh and get fresh data into our system. A scheduled run is on the roadmap but not today.
- **Partners are not reliable.** Both will be down occasionally and one being down must not block the other
- **Don't hammer the partners.** If ops fat-fingers the trigger twice in two minutes, we shouldn't double our load on the partner — or double our data.
- **The next engineer needs to be able to pick this up.** Whatever you ship, ship it so it's understandable.

If something here is contradictory or under-specified, that's also normal. See "Gaps" below.

---

## Delivery score

Ops have already fixed the formula. Every delivery carries a score:

```
deliveryScore = (signed ? 1.0 : 0.3) × (isMorning ? 1.2 : 1.0)
```

- `signed`: your normalized boolean for whether the delivery was signed for. Figure out how to derive it from each partner's payload.
- `isMorning`: the delivery happened between 05:00 and 11:00 UTC.

Higher score = higher priority. Score is the default sort order wherever ops sees deliveries.

---

## What we're giving you

- A `docker-compose.yml` that brings up two mock partner APIs behind a Traefik proxy. Each mock exposes its own Swagger UI — go read it. The mocks behave roughly like the real partners: different request/response shapes, no useful input parameters, and occasional flakiness (failure rate and slow-response rate are tunable per partner via `.env`).
- A backend scaffold (`backend/`) with FastAPI, a Dockerfile, and a `start.sh`. You may modify it freely or replace parts you don't like.

We're deliberately not documenting the partner response shapes here. Open their Swagger, look at their sample data.

---

## Firm technical constraints

These are the non-negotiables. Everything else is a design decision you own.

- **Language**: Python 3.11+ (3.10 minimum).
- **Framework**: FastAPI.
- **Persistence**: a SQL-based store. Postgres, MySQL, SQLite — your call.
- **Containerized**: runnable via Docker (or Podman) with a single command.
- **Entry point**: a `start.sh` that respects at minimum these env vars:
  - `HTTP_PORT`
  - `LOGISTICS_A_URL`
  - `LOGISTICS_B_URL`
  - `DATABASE_URL`

Everything else — API shape, internal model, async strategy, libraries, code layout, test framework — is yours to decide.

---

## What we actually evaluate

We are **not** scoring you on feature coverage, lines of code, or how many "best practices" you've ticked.

We care about:

- **Decisions.** What did you choose, what did you reject, and why? Where did you trade depth for time? What did you deliberately leave out?
- **The trust boundary.** Where does data you don't control become data you do? How do you handle a partner record that's malformed, late, or just wrong? How do you handle one partner being down?
- **Naming your own risk.** What in this codebase would scare you if it shipped to prod tomorrow?
- **Restraint.** Small modules with clear ownership. No code you wouldn't want to maintain in three months.
- **The README.** The README is part of the submission. It should let a colleague pick this up without you in the room: architecture, key decisions, known gaps, what you'd do next.
- **Tests.** We expect tests across the system, not only the bits you flagged as risky. Happy paths, edge cases, error handling, the normalization rules — a reviewer should be able to read the test suite and trust the thing works.

---

## Gaps (intentional)

This brief has holes and that's on purpose. Resolve them by making a call, writing it down in the README under "Assumptions," and moving on.

Some examples — there are more:

- "Don't double-fetch" — what counts as the same fetch? Over what window?
- Reading the data back: what filters, what sorting, what pagination are actually needed? What's premature?
- A malformed partner record — skip it, surface it, fail the job? What does the consumer of that job need to know?
- What does "fresh enough" mean? Does ops want a synchronous answer, or are they fine waiting for a job to finish?
- The mocks return everything at once. Real partners eventually won't. Does that affect anything you'd build today, or is that a future-you problem?

We're not going to tell you the right answer. Make a call. Don't try to resolve everything in code — some of these answers belong in the README as "next" or "not solving today."

---

## Out of scope

Don't spend time on:

- Authentication, authorization, or anything user-identity-related.
- Multi-tenancy.
- Real production infra: k8s, observability stack, secret management, etc.
- A frontend.

---

## On AI tools

We expect you'll use them — pick whichever coding agent you're comfortable with. If you'd rather not, that's also fine; we care about the result, not the tooling.

We'll read the code, then talk through it.

---

## Submission

A repo (GitHub / GitLab / Bitbucket) or a zip with:

- The service, runnable per the constraints above.
- A README covering: how to run it, your architecture, the decisions you made and why, the gaps you identified and how you resolved them, what you'd build next, what you'd change if this were going to production.
- Tests covering the system, not just the risky bits — normalization, scoring, error paths, the read API, all of it.

If you find yourself spending more than 3-4 hrs, stop, and write down what you'd do with more time. The "what's next" section is a real evaluation surface — being able to articulate gaps is valuable.

After you submit, expect a ~15-30-minute walkthrough where we go through the codebase together and pull on the decisions.

---

## Orientation (not a contract)

So you have a mental model: operations triggers a fetch, the service pulls from both partners, normalizes and stores the data, and then operations or site managers query the results. Whatever shape that takes — REST, jobs, sync, async, your model, your endpoints — is for you to decide.

Build what makes sense.
