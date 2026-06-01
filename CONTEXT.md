# Open Chat Studio

A multi-tenant platform for building, deploying, and evaluating conversational AI agents. Domain experts configure agents; the platform runs conversations across messaging channels, captures traces, and supports evaluation against curated datasets.

## Language

### Chatbots and pipelines

**Chatbot**:
The logical conversational agent — the family of versions that users build, publish, and evaluate. A Chatbot has zero or more **Chatbot Versions**.
_Avoid_: Experiment (legacy code-only term).

**Chatbot Version**:
An immutable snapshot of a Chatbot's configuration. Sessions, channels, and evaluation runs are bound to a specific Chatbot Version, not the family.
_Avoid_: "the Experiment", when what you mean is a particular version row.

**Working Version**:
The single editable draft of a Chatbot — the head of the family, where edits land before they're promoted into a snapshot. Identified in code by `working_version_id IS NULL`.

**Published Version**:
The Chatbot (or Pipeline) Version that external channels and APIs serve. Exactly one per family. Becoming the Published Version is a **choice made at snapshot time** (`make_default` on `create_new_version`), not an automatic consequence of snapshotting — the one exception is the very first version, which is published automatically. When a new snapshot is published, the previous Published Version is demoted. So "snapshot" and "publish" are separable: a Working Version can be snapshotted into an immutable Chatbot Version *without* that version going live.
_Avoid_: Default Version (the underlying field is `is_default_version`, but new writing should say Published Version to match the UI).

**Pipeline**:
The DAG of nodes that defines a Chatbot's runtime behaviour, edited in a visual flow editor. Every Chatbot has one Pipeline.

**Pipeline Version**:
An immutable snapshot of a Pipeline. Each Chatbot Version owns exactly one Pipeline Version (snapshot-paired, 1:1). A Pipeline Version is **not** independently published — there is no Published Pipeline Version concept. Whichever Pipeline Version is paired with the Published Chatbot Version is the one external Channels execute.

**Pipeline Node**:
A single node in a Pipeline's DAG. Each Node has a type (Start, End, LLM, Assistant, Router, Custom Action, …) that determines its behaviour, plus a `params` JSON blob configured via the visual editor. Nodes are independently versioned alongside their Pipeline.

**Version Selection Rule**:
The rule a caller uses to ask "given a Chatbot family, which Chatbot Version do I want?". Three values: **Specific** (pinned by `version_number` within the family), **Latest Working**, **Latest Published**. Used by Evaluation Configs, channel entry-point tasks, the API entry point, and the web widget. Resolved at the moment of use against the family head.
_Avoid_: "version selection type" (the legacy field name on `EvaluationConfig`).

### Runtime

**Channel**:
A binding between a Chatbot and an external messaging platform (Telegram, WhatsApp, Slack, web widget, email, CommCare Connect). One Chatbot has many Channels. Backed by the `ExperimentChannel` model — that's a legacy code name only.
_Avoid_: "ExperimentChannel" outside of code.

**Session**:
A single conversation between a Participant and a Chatbot Version, conducted via a Channel (or via the API entry point, web widget, or an evaluation run). Materialised in code as an `ExperimentSession` row paired 1:1 with a `Chat` row that holds the message log; treat them as one domain concept.
_Avoid_: "Chat" (overloads with the `Chat` model and a verb), "Conversation" (casual UI phrasing).

### Events and triggers

**Static Trigger**:
A rule attached to a Chatbot that fires an **Event Action** when a conversation *event* occurs — the conversation ends, a new human/bot message arrives, a participant joins, etc. Versioned alongside the Chatbot.

**Timeout Trigger**:
A rule attached to a Chatbot that fires an **Event Action** after a period of participant inactivity (a delay in seconds), optionally repeating a fixed number of times. The canonical case is a 24-hour inactivity re-engagement message.
_Note_: Triggers attach to the **Chatbot**, not to the **Pipeline** graph — they are not Pipeline Nodes.

**Event Action**:
What a Trigger does when it fires — one of: log, end the conversation, send a message to the bot, start a Pipeline, or schedule a trigger. Carries a freeform `params` payload.

### People and tenancy

**Participant**:
A chat-user identity bound to a single platform within a Team — uniquely identified by `(team, platform, identifier)`. The same human reaching a Chatbot via Telegram and WhatsApp produces two Participants.

**User**:
A registered Django auth user — a builder, evaluator, or admin who logs in to OCS to configure Chatbots, run evaluations, etc. Distinct from a Participant. A Participant may optionally link to a User via `Participant.user`.
_Avoid_: "User" when you mean a Participant. Build the habit of saying which.

**Team**:
The multi-tenancy root scope. Almost every domain resource (Chatbot, Pipeline, Channel, Session, Participant, Service Provider) belongs to exactly one Team. Users join Teams to gain access; multi-tenancy plumbing lives in `docs/agents/multi_tenancy.md`.

### Integrations

**Service Provider**:
A Team-scoped record holding credentials and configuration for one external integration. Kinds: **LLM Provider** (OpenAI, Anthropic, Groq, Gemini, Azure, …), **Messaging Provider** (Twilio, Telegram, Slack credentials, …), **Voice Provider** (TTS/STT services), **Auth Provider** (OAuth/SAML, used by Custom Actions and sign-in), and **Trace Provider** (observability backends). The LLM, Voice, and Messaging kinds share a `ProviderMixin`; Auth and Trace providers do not. The encrypted credential blob lives in a `config` field and is never exposed externally.

**LLM Provider Model**:
A specific model offering a **Provider** can serve — e.g. `gpt-4o`, `claude-opus-4` — with its own token limit. Distinct from the **LLM Provider**: a Provider is the credentialed account, a Model is a catalogue entry. They are **independent rows joined by provider `type`, not a foreign key**, and a Pipeline Node selects *both* — a Provider and a Model. May be Team-scoped or a global (Team-less) catalogue row. The same Provider/Model split applies to embeddings (an **Embedding Provider Model** paired with a Provider).
_Avoid_: conflating "Provider" and "Model" — choosing a bot's LLM means choosing both.

**OpenAI Assistant**:
A Team-scoped, versioned wrapper around a resource in OpenAI's Assistants API. Pipelines invoke one via an `AssistantNode`.
_Avoid_: bare "Assistant" — it overloads with the colloquial sense ("the chatbot as an assistant").

**Custom Action**:
A Team-scoped HTTP API that a Chatbot can call, described by an OpenAPI schema. Provides one or more **Custom Action Operations**.

**Custom Action Operation**:
A single callable endpoint within a Custom Action's OpenAPI schema. Pipelines reference Operations, not whole Custom Actions.

### Observability

**Trace**:
The logical execution record for one Chatbot turn — a single message in, response out. Captures timing, inputs, and outputs end-to-end.

**Span**:
A step within a Trace (one LLM call, one tool call, one Custom Action invocation, etc.). Many Spans per Trace.

### Evaluations

**Evaluator**:
A judge that scores or tags Chatbot output — implementations include LLM-as-judge, regex match, tag-rule, etc. Each Evaluator has an **Evaluation Mode**: `message` (judges one message pair) or `session` (judges a whole Session).

**Evaluation Dataset**:
A curated, Team-scoped collection of **Evaluation Messages** to evaluate against. Built asynchronously (status field tracks progress); typically derived from real Sessions but can be authored directly. Carries an Evaluation Mode that constrains the contained messages.

**Evaluation Message**:
A single test case in an Evaluation Dataset. In `message` mode it's an input/expected pair; in `session` mode it's a whole conversation transcript.

**Evaluation Config**:
The recipe for an evaluation: one Evaluation Dataset, many Evaluators, and a rule for picking which Chatbot Version to generate outputs against — `specific`, `latest_working`, or `latest_published`. Resolved at run time, pinned on the Run.

**Evaluation Run**:
One execution of an Evaluation Config. Has a status (pending / processing / completed / failed) and a type: `full` (whole dataset) or `preview` (sample). Pins the resolved Chatbot Version that generated outputs as `generation_experiment`.

**Evaluation Result**:
The output of one Evaluator scoring one Evaluation Message within an Evaluation Run.

## Relationships

- A **Chatbot** has many **Chatbot Versions** (one per snapshot).
- Each **Chatbot** has exactly one **Working Version** and at most one **Published Version**.
- The **Working Version** can also be the **Published Version** before any snapshots have been promoted.
- A **Pipeline** has many **Pipeline Versions**. Working Version semantics apply identically; **Published Version does not** — Published-ness lives on the Chatbot Version, and the paired Pipeline Version inherits it.
- A **Chatbot Version** owns exactly one **Pipeline Version** (1:1, snapshot-paired): publishing a new Chatbot Version creates a new Pipeline Version alongside it.
- A **Session** is bound to one **Chatbot Version**, one **Participant**, and (optionally) one **Channel**.
- A **Channel** stores an FK to a Chatbot's family head; at message-receipt time it routes to that Chatbot's **Published Version**, and the resulting Session is bound to that version. The web widget and API can override with an explicit version number, which is how teams "chat with the Working Version" for testing.
- A **Participant** belongs to one **Team** and one platform; the same human across two platforms is two **Participants**.
- An **Evaluation Run** generates outputs against one **Chatbot Version**, resolved from its **Evaluation Config** at run time and pinned on the Run. Same machinery as Channel routing — Working vs Published is decided per-Config.
- A **Pipeline Node** of type `AssistantNode` references one **OpenAI Assistant**; pipelines also reference **Custom Action Operations** to make outbound HTTP calls.
- **Static Triggers** and **Timeout Triggers** attach to a **Chatbot Version**, not to its **Pipeline** — so reasoning about "what a published bot does" must include both the Pipeline graph and the Chatbot's Triggers.
- **Snapshotted vs live on publish.** Creating a **Chatbot Version** snapshots the Pipeline, its Nodes, the Triggers, and the *versioned* resources they reference (**Source Material**, **Collections**, **OpenAI Assistants**, **Custom Action Operations**). **Service Providers** and **LLM Provider Models** are **not** versioned — they are shared, live rows — so a Published Version reflects their *current* configuration, not a frozen copy.

## Example dialogue

> **Builder:** "I just edited my **Chatbot** but Telegram users are still getting old responses."
> **Engineer:** "External Channels serve the **Published Version**. Your edits landed in the **Working Version** — you need to publish a new **Chatbot Version** before Telegram will pick them up. You can test the Working Version right now from the web widget."
>
> **Builder:** "Why does my evaluation report two **Sessions** for one tester who chatted on Telegram and WhatsApp? They were the same person."
> **Engineer:** "A **Participant** is platform-bound, so the same human on Telegram and WhatsApp is two Participants — and each one's conversation is its own Session. If you want them collapsed, you'd need to correlate by their **User** account, but that requires the participant to be linked to one."

## Flagged ambiguities

- "Chatbot" vs "Experiment" — resolved: **Chatbot** is the canonical domain term. **Experiment** survives as the historical model/app name in code and migrations only; user-facing copy, issues, PRDs, and new docs should use **Chatbot**.
- "Chatbot" used for both the family and a specific version row — resolved: **Chatbot** = the family; **Chatbot Version** = a snapshot. When in doubt, say which.
- "Default Version" (code) vs "Published Version" (UI) — resolved: same concept; **Published Version** is canonical. The model field stays `is_default_version`; new writing says published.
- **API / WEB / EVALUATIONS pseudo-platforms**: rows in the `ExperimentChannel.platform` enum that share the channel storage but aren't user-configurable Channels (excluded from the platform dropdown). When discussing entry points for messages, distinguish "a Channel" (real platform) from "the API entry point", "the web widget", and "an evaluation run" — these are not Channels in the domain sense.
- **Participant ≠ Person, Participant ≠ User**: a Participant is platform-bound, so one human can be many Participants. A **User** is a registered platform user (auth, login). When the audience might collapse them, name which one you mean explicitly.
