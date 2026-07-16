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
├── part-1-foundations/             # Primitives & core building blocks
│   ├── 01-state/                   # Thread-safe, immutable, versioned state stores
│   ├── 02-agent-loop/              # Stable, model-independent execution cycles
│   ├── 03-tool-calling/            # Schema validation & secure tool dispatchers
│   └── ...                         # Structured Output, Memory, Planner, Evals, etc.
│
├── part-2-production-patterns/     # High-scale production engineering concerns
│   ├── retry/                      # Backoffs and transient error management
│   ├── checkpointing/              # Mid-run state persistence & durability
│   ├── queues/                     # Decoupling workflows with workers & brokers
│   └── ...                         # Redis caching, Postgres database, Observability
│
├── part-3-complete-systems/        # Combined production architectures
│   └── healthcare-platform/        # Flagship multi-service enterprise application
│
├── common/                         # Shared helper libraries
├── docker/                         # Multi-stage Docker & Compose setups
└── web/                            # Interactive learning visualizer dashboard (Next.js)
```

Each lesson contains:
1. **README.md**: Concepts, beginner vs. production implementations, tradeoffs, and failure modes.
2. **architecture.md**: Diagrams explaining execution flows and component interactions.
3. **example.py**: Strong-typed, runnable production implementation.
4. **tests.py**: Automated tests verifying correctness and concurrency bounds.

---

## ⚡ Interactive Learning UI

We provide a beautiful, dynamic developer dashboard to visualize how the agent loop functions, compare code versions, and examine architecture traces.

### Running the UI Locally

1. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Setup and run the Next.js Frontend**:
   ```bash
   cd web
   npm install
   npm run dev
   ```
   Open [http://localhost:3000](http://localhost:3000) in your browser to explore the interactive lessons.

3. **Re-compiling Content**:
   If you modify any lesson code or READMEs, compile the database by running:
   ```bash
   npm run extract
   ```

---

## 🚀 Future Vision

Production AI Engineering is designed to work in two modes:

### 1. Learning Mode
* Runs entirely statically from **GitHub Pages**.
* No backend or Docker setup required.
* Read conceptual documentation, study source code implementations, browse architecture maps, and view pre-rendered animations/traces directly in the browser.

### 2. Interactive Mode
Launch any complete production example or service locally with a single command:
```bash
docker compose up
```
The documentation website automatically pings and detects the running local backend (`/health` endpoint) and transforms the static textbook page into a **live, interactive playground**.

Interactive features enabled dynamically include:
* **Live Workflow Execution**: Run prompts and watch the agent navigate tools in real-time.
* **Background Worker & Queue Monitoring**: Inspect pending task queues and follow background workers processing asynchronous workflows.
* **Retry & Compaction Visualization**: Watch model fallback cascades, exponential backoff timers, and memory compaction events occur on actual runs.
* **Memory & Persisted State Inspection**: Browse variables, state rollbacks, and episodic memory records stored in the active PostgreSQL/Redis database container.
* **OpenTelemetry Trace Telemetry**: Profile latencies, query timings, and prompt token costs directly in the UI.
* **API Playground & Swagger Integration**: Test individual microservice endpoints directly from the interactive docs.

#### Launching a Live Demo
When viewing a lesson, simply click the **🚀 Launch Live Demo** button. The documentation will explain:
> *"Run `docker compose up` in `examples/healthcare/` (or the relevant chapter directory) and refresh this page."*

Once the site successfully pings and identifies the backend container, the static page automatically switches to live interactive mode.
