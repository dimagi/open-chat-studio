# Open Chat Studio: Vision

## What This Is

Open Chat Studio is an open-source platform for building, deploying, and evaluating AI chatbots. It's built by Dimagi, a company that's spent 20+ years building technology for global health and social services. That background shapes how we think about this tool. The people on the other end of these chatbots are real, and they deserve software that works well for them.

## Who It's For

Teams that need to put AI chat in front of real people and make sure it actually works. That includes health organizations, NGOs, research teams, and anyone who wants to self-host rather than depend on a closed platform.

## What It Does Well

**Evaluation is built in, not bolted on.** You deploy a chatbot, measure how it's doing (automated evals, human review), and improve it. That loop is the point.

**Pipelines over monolithic prompts.** Visual workflows let you wire together LLM calls, routing logic, code execution, and API calls. Easier to reason about, easier to change.

**Deploy once, run anywhere.** Same chatbot works across WhatsApp, Telegram, Slack, web, and API. The platform handles the channel differences.

**Safe iteration.** Working versions and published versions mean you can experiment without breaking what's live.

**Team isolation.** Multi-tenancy, feature flags, and access controls at every layer, from the start.

## Where We're Going

We want the full lifecycle of a conversational AI app to happen in one place: prototype, deploy, evaluate, improve. Shorter feedback loops. More capable pipelines. Better integration with the tools teams already use.

---

## How We Build

### Toward a code factory

We're building toward a development model where AI agents do the bulk of coding and review, and humans provide direction, judgment, and oversight. We're not there yet. Today we use Claude Code for implementation and Igor for picking up routine tasks overnight. To get where we're going, we need better safety gates: stronger CI, better test coverage, architectural guardrails that make it hard for an AI agent to break things. The codebase itself needs to evolve. Cleaner boundaries, less implicit coupling, more refactoring to make the code legible and safe for autonomous agents to work in.

### Build incrementally, release when ready

Feature flags scoped to teams let us build in small pieces and control when something goes live. A feature can be merged and deployed without being visible to users. This matters even more as AI agents do more of the work. Flags are one of the gates that keep half-finished work from reaching users.

### Evaluate everything

The same philosophy we apply to chatbots applies to how we build. If we can't measure whether something is better, we're guessing. Automated tests, CI gates, and code review (human and AI) are the minimum. As we hand more coding to agents, these checks become load-bearing. They're what give us confidence to move fast.

### Simplicity over cleverness

We favor straightforward code that's easy to read and change. Three similar lines are better than a premature abstraction. Simple code is safer code, especially when AI agents are the ones reading and modifying it. A lot of the refactoring ahead of us is about getting to that simplicity.

### Open source means open process

The code is public. Contributors should be able to understand not just what we built but why. We keep the PR template honest and document decisions where it makes sense.
