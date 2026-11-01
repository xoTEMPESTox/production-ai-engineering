# Production AI Engineering

### Learn how production AI systems are designed, built, deployed, and maintained.

---

## 🌟 The Vision

This repository is **not** another AI framework, toy chatbot, or basic RAG template. 

**Its goal is to teach production AI engineering.**

There are countless tutorials showing how to build an agent in 100 lines of code. Very few explain how that agent evolves into a production system with retries, checkpoints, queues, background workers, state persistence, deployments, monitoring, evaluation, and long-running workflows. 

**This repository bridges that gap.**

Every chapter starts with a minimal educational example and gradually evolves into a production implementation. The final chapters combine everything into complete AI platforms inspired by real-world engineering experience.

---

## 🎯 Target Audience
* **Backend Engineers** wanting to build robust, scalable infrastructures for AI systems.
* **AI & LLM Engineers** moving from toy prototypes/prompt-plumbing to resilient production agents.
* **Computer Science Students** looking to understand modern software engineering in the era of foundation models.

---

## 🧠 Philosophy: Engineering over Frameworks

We focus on the underlying **engineering principles** rather than specific vendor frameworks (like LangChain or LangGraph). The concepts taught here remain valid regardless of the orchestration framework or LLM API you choose.

Every chapter answers:
> *"How would this actually be built and run at scale in production?"*

* **Readability over brevity**: We never optimize for fewer lines of code. Every line is written to reflect production standards (strong typing, clean architecture, separation of concerns).
* **Self-Contained & Original**: No copied prompts, no proprietary business logic, no framework lock-in.

---

## 🗺️ Repository Structure & Progression

```
production-ai-engineering/
├── part-1-foundations/             # Primitives & core building blocks (f01–f15)
│   ├── 01-state/                   # Thread-safe, immutable, versioned state stores
│   ├── 02-agent-loop/              # Stable, model-independent execution cycles
│   └── ...                         # Prompts, Tool calling, Memory, HITL, Scheduling
│
├── part-2-production-patterns/     # High-scale production engineering concerns (p16–p30)
│   ├── 16-retry-patterns/          # Transient error backoffs & jitter policies
│   ├── 17-background-workers/      # Offloaded async computations
│   └── ...                         # Redis checkpoints, Postgres state database, queues
│
├── part-3-complete-systems/        # Combined production architectures (c01–c04)
│   ├── healthcare-platform/        # Planned flagship (not yet written)
│   └── ...                         # Support engine, research pipeline, coding agent
│
├── part-4-infrastructure/          # Multi-container local environments (i01)
│   └── local-compose-deployment/   # Planned Docker Compose setup (not yet written)
│
└── web/                            # Interactive Next.js visualizer documentation dashboard
```

Every lesson is self-contained: it imports nothing from other lessons, so you can open any folder and run it.

Each lesson in Part 1 and Part 2 contains:
1. **README.md**: Conceptual definitions, naive vs. production architectures, and design tradeoffs.
2. **architecture.md**: Diagrams explaining execution flows and component interfaces.
3. **example.py**: Strong-typed, runnable production-grade Python implementation.
4. **tests.py**: Automated tests verifying correctness and concurrency bounds.

---

## 🏁 Getting Started

You need Python 3.10 or newer (Lesson 21 uses `contextlib.aclosing`, added in 3.10).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cd part-1-foundations/02-agent-loop
python example.py                  # a short demo
python -m pytest tests.py -q       # or: python tests.py
```

No API key, network or running server is needed. Model calls go through a small `LLMClient` interface with a scripted fake, and the Redis and PostgreSQL lessons use in-memory versions of the same interface. To also test Lessons 18 and 19 against real servers, set `PAIE_REDIS_URL` or `PAIE_POSTGRES_URL`; otherwise those tests are skipped. Each lesson's README ends with a **Try It** section: what to look for in the output, and a one-line change that makes a named test fail.

---

## 📚 Final Curriculum

> [!NOTE]
> **Roadmap Status**: Lessons **01–30** and three of the four complete systems are implemented, each with a runnable `example.py` and offline `tests.py`. The Healthcare Platform and Local Compose Deployment are still skeletons. The curriculum covers three pillars throughout: **Agent** (loops, tools, planning, coordination), **RAG** (retrieval, grounding, citations) and **Harness**, meaning everything around the model call that makes it safe to run (budgets, validation, evals, traces, cost).

### PART I — Foundations (Learn AI Systems)
Goal: Learn how an AI system thinks before learning how it survives production. Each chapter's example is about 140–290 lines: runnable, concept-focused, and highly educational.

| # | Chapter | Key Focus / Primitives | Status |
|---|---------|------------------------|--------|
| 01 | [State Representation](part-1-foundations/01-state) | Thread-safety, copy-on-write immutability & audit rollbacks | ✅ Completed |
| 02 | [The Agent Loop](part-1-foundations/02-agent-loop) | Bounded think-act-observe: step/token/time budgets, loop detection, typed events | ✅ Completed |
| 03 | [Prompting & Structured Output](part-1-foundations/03-prompting-structured-output) | Versioned prompts, Pydantic contracts, citation checks, repair once then fail closed | ✅ Completed |
| 04 | [Tool Calling](part-1-foundations/04-tool-calling) | JSON-schema tool specs, validation, permission tiers, idempotency keys, timeouts, MCP | ✅ Completed |
| 05 | [Memory & Retrieval](part-1-foundations/05-memory) | The RAG core: tenant-filtered hybrid search (BM25 + vectors, RRF), write policy, compaction | ✅ Completed |
| 06 | [Planning & Task Decomposition](part-1-foundations/06-planning-task-decomposition) | The plan as validated data, progress tracking, bounded re-planning | ✅ Completed |
| 07 | [Multi-Agent Coordination](part-1-foundations/07-multi-agent-coordination) | Isolated sub-agents, typed hand-offs, bounded fan-out, checked fan-in | ✅ Completed |
| 08 | [Human Approval (HITL)](part-1-foundations/08-human-approval) | Pause/resume approvals, edit validation, escalation, expiry, audit trail | ✅ Completed |
| 09 | [Evaluation & Self Reflection](part-1-foundations/09-evaluation-self-reflection) | Golden sets, Recall@k/MRR, calibrated LLM judges, completeness outside the model | ✅ Completed |
| 10 | [Scheduling & Long Running Tasks](part-1-foundations/10-scheduling-long-running-tasks) | Leases, heartbeats, per-batch checkpoints, fencing tokens, coalesced schedules | ✅ Completed |
| 11 | [Async Execution](part-1-foundations/11-async-execution) | asyncio with semaphores, timeouts, structured cancellation, backpressure | ✅ Completed |
| 12 | [Event Driven Agents](part-1-foundations/12-event-driven-agents) | Transactional outbox, at-least-once delivery, idempotent versioned consumers | ✅ Completed |
| 13 | [State Machines](part-1-foundations/13-state-machines) | Guarded transitions and per-key merge reducers (the dropped-branch bug) | ✅ Completed |
| 14 | [Error Handling](part-1-foundations/14-error-handling) | Error taxonomy, labelled fallbacks never cached, circuit breakers | ✅ Completed |
| 15 | [Persistence](part-1-foundations/15-persistence) | SQLite checkpoints per step, resume and rewind, schema migrations | ✅ Completed |

---

### PART II — Production Patterns
Goal: Learn how to scale, secure, monitor, and configure agents in a production environment.

| # | Chapter | Key Focus / Primitives | Status |
|---|---------|------------------------|--------|
| 16 | [Retry Patterns](part-2-production-patterns/16-retry-patterns) | Full-jitter backoff, Retry-After, deadlines, retry budgets, one idempotency key | ✅ Completed |
| 17 | [Background Workers](part-2-production-patterns/17-background-workers) | Visibility timeouts, dead-letter queue, single-flight per record | ✅ Completed |
| 18 | [Redis Checkpointing](part-2-production-patterns/18-redis-checkpointing) | Key design, expiring checkpoints, token-owned SET NX PX locks | ✅ Completed |
| 19 | [PostgreSQL State Store](part-2-production-patterns/19-postgresql-state-store) | Runs/steps/events tables, optimistic concurrency, SKIP LOCKED job claims | ✅ Completed |
| 20 | [Queues](part-2-production-patterns/20-queues) | Partitioned log, per-key ordering, consumer groups, commit after processing | ✅ Completed |
| 21 | [Streaming](part-2-production-patterns/21-streaming) | SSE frames, streaming partial JSON, retract on failure, TTFT, real cancellation | ✅ Completed |
| 22 | [Observability](part-2-production-patterns/22-observability) | Per-step spans with GenAI attributes, cost, redacted sampling, traces to evals | ✅ Completed |
| 23 | [Docker](part-2-production-patterns/23-docker) | Multi-stage non-root images, liveness vs readiness, checksummed artifacts | ✅ Completed |
| 24 | [Deployment](part-2-production-patterns/24-deployment) | Eval gate, shadow traffic, sticky canary, guardrail rollback, warm starts | ✅ Completed |
| 25 | [Configuration](part-2-production-patterns/25-configuration) | Typed fail-fast settings, SecretStr secrets, audited prompt/model flags | ✅ Completed |
| 26 | [Monitoring](part-2-production-patterns/26-monitoring) | SLO burn-rate alerts, AI quality signals, alert dedupe, PSI drift | ✅ Completed |
| 27 | [Evaluation Pipelines](part-2-production-patterns/27-evaluation-pipelines) | Cached outputs, bootstrap confidence intervals, paired per-slice regression gate, online sampling | ✅ Completed |
| 28 | [Cost Tracking](part-2-production-patterns/28-cost-tracking) | Per-call ledger, reserve-then-settle budgets, cache, cascades, batch | ✅ Completed |
| 29 | [Security](part-2-production-patterns/29-security) | Indirect prompt injection, taint-aware least privilege, no eval, PII vault | ✅ Completed |
| 30 | [Scaling](part-2-production-patterns/30-scaling) | Rate-aware gateway with spill-over, AIMD, micro-batching, single-flight cache | ✅ Completed |

---

### PART III — Complete Systems
Goal: Reference implementations of complete production applications built using the foundations and patterns.

| System | Key Focus / Primitives | Status |
|--------|------------------------|--------|
| **[Healthcare Platform](part-3-complete-systems/healthcare-platform) (Flagship ⭐)** | HIPAA-inspired agent integrating intake queues, Redis checkpoints, human review gates, and PostgreSQL audits | ⏳ Skeleton |
| **[Customer Support](part-3-complete-systems/customer-support)** | Rules-first routing, cross-channel resume, refund limits, human hand-off | ✅ Completed |
| **[Research Assistant](part-3-complete-systems/research-assistant)** | Planned sub-questions, scoped retrieval, verified sub-agent findings, honest gaps | ✅ Completed |
| **[Coding Agent](part-3-complete-systems/coding-agent)** | Disposable workspace, read-only tests, scrubbed sandbox, test-and-repair loop | ✅ Completed |

---

### PART IV — Infrastructure
| System | Key Focus / Primitives | Status |
|--------|------------------------|--------|
| **[Local Compose Deployment](part-4-infrastructure/local-compose-deployment)** | Docker Compose specifications spinning up Redis caching, PostgreSQL databases, worker queues, Swagger endpoints, and real-time dashboard telemetry | ⏳ Skeleton |

---

## ⚡ Interactive Learning UI

We provide a beautiful, dynamic developer dashboard to visualize how the agent loop functions, compare code versions, and examine architecture traces.

### Running the UI Locally

The UI needs only Node.js; it reads the lesson files directly.

1. **Setup and run the Next.js Frontend**:
   ```bash
   cd web
   npm install
   npm run dev
   ```
   Open [http://localhost:3000](http://localhost:3000) in your browser to explore the interactive lessons.

2. **Re-compiling Content**:
   If you modify any lesson code or READMEs, compile the database by running:
   ```bash
   npm run extract
   ```

---

## 🚀 Future Vision

Production AI Engineering is planned to work in two modes. **Learning Mode works today; Interactive Mode is not built yet.**

### 1. Learning Mode (available now)
* Runs entirely statically from **GitHub Pages**.
* No backend or Docker setup required.
* Read conceptual documentation, study source code implementations, browse architecture maps, and view pre-rendered animations/traces directly in the browser.

### 2. Interactive Mode (planned)
The plan is to launch a complete example locally with a single command:
```bash
docker compose up
```
The documentation site would then detect the running backend (through a `/health` endpoint) and turn the static page into a **live, interactive playground**.

Planned interactive features:
* **Live Workflow Execution**: Run prompts and watch the agent navigate tools in real-time.
* **Background Worker & Queue Monitoring**: Inspect pending task queues and follow background workers processing asynchronous workflows.
* **Retry & Compaction Visualization**: Watch model fallback cascades, exponential backoff timers, and memory compaction events occur on actual runs.
* **Memory & Persisted State Inspection**: Browse variables, state rollbacks, and episodic memory records stored in the active PostgreSQL/Redis database container.
* **OpenTelemetry Trace Telemetry**: Profile latencies, query timings, and prompt token costs directly in the UI.
* **API Playground & Swagger Integration**: Test individual microservice endpoints directly from the interactive docs.

Each lesson page would get a **🚀 Launch Live Demo** button explaining which folder to run `docker compose up` in; once the site detects the backend, the page switches to live mode.

---

## 🤝 Acknowledgements

This project's educational website and progressive learning experience were inspired by the excellent [Learn Claude Code](https://github.com/shareAI-lab/learn-claude-code) project. Their progressive documentation style heavily influenced the organization of this repository. All curriculum, code, examples, architecture diagrams, and production implementations in this repository are original and focused on production-grade AI engineering.
