import type { AgentLayer } from "@/types/agent-data";

export const VERSION_ORDER = [
  // Part 1: Foundations
  "f01",
  "f02",
  "f03",
  "f04",
  "f05",
  "f06",
  "f07",
  "f08",
  "f09",
  "f10",
  "f11",
  "f12",
  "f13",
  "f14",
  "f15",

  // Part 2: Production Patterns
  "p16",
  "p17",
  "p18",
  "p19",
  "p20",
  "p21",
  "p22",
  "p23",
  "p24",
  "p25",
  "p26",
  "p27",
  "p28",
  "p29",
  "p30",

  // Part 3: Complete Systems
  "c01",
  "c02",
  "c03",
  "c04",

  // Part 4: Infrastructure
  "i01",
] as const;

export const LEARNING_PATH = VERSION_ORDER;

export type VersionId = typeof LEARNING_PATH[number];

export const VERSION_META: Record<string, {
  title: string;
  subtitle: string;
  coreAddition: string;
  keyInsight: string;
  layer: AgentLayer;
  prevVersion: string | null;
}> = {
  // Part 1: Foundations
  f01: {
    title: "State Representation",
    subtitle: "The Foundation of Agent Execution",
    coreAddition: "Immutable State Logs",
    keyInsight: "Robust agents require immutable state logs to support concurrency, trace execution, and recover from failures.",
    layer: "foundations",
    prevVersion: null,
  },
  f02: {
    title: "Agent Loop",
    subtitle: "Bounded Think-Act-Observe",
    coreAddition: "Budgets, Loop Detection & Typed Events",
    keyInsight: "The model proposes the next action; the harness decides whether it runs, within step, token and time budgets.",
    layer: "foundations",
    prevVersion: "f01",
  },
  f03: {
    title: "Prompting & Structured Output",
    subtitle: "Prompts as Versioned Contracts",
    coreAddition: "Validate, Repair Once, Fail Closed",
    keyInsight: "Schema validation is not enough for RAG: citations must be checked against the sources the request actually sent.",
    layer: "foundations",
    prevVersion: "f02",
  },
  f04: {
    title: "Tool Calling",
    subtitle: "One Pipeline for Every Call",
    coreAddition: "Typed Specs, Tiers & Idempotency Keys",
    keyInsight: "Validate arguments, check permission on the validated call, deduplicate writes, bound time and size: in that order.",
    layer: "foundations",
    prevVersion: "f03",
  },
  f05: {
    title: "Memory & Retrieval",
    subtitle: "The RAG Core",
    coreAddition: "Tenant-Filtered Hybrid Search",
    keyInsight: "Filter by who is asking, then rank by what they asked: BM25 plus vectors fused with RRF, and a write policy for memories.",
    layer: "foundations",
    prevVersion: "f04",
  },
  f06: {
    title: "Planning & Task Decomposition",
    subtitle: "The Plan Is Data",
    coreAddition: "Validated Todo List & Bounded Re-planning",
    keyInsight: "Re-plan only the remaining work: finished side effects are never redone, and re-plans are capped.",
    layer: "foundations",
    prevVersion: "f05",
  },
  f07: {
    title: "Multi-Agent Coordination",
    subtitle: "Isolated Sub-agents, Typed Hand-offs",
    coreAddition: "Bounded Fan-out, Checked Fan-in",
    keyInsight: "The harness, not the model, decides what is missing, and the final answer may only cite what a verified finding cited.",
    layer: "foundations",
    prevVersion: "f06",
  },
  f08: {
    title: "Human Approval",
    subtitle: "Pause the Run, Not the Thread",
    coreAddition: "Persisted Approval Requests",
    keyInsight: "Approve, edit or reject, decided once by the right role; silence escalates and expires as a rejection.",
    layer: "foundations",
    prevVersion: "f07",
  },
  f09: {
    title: "Evaluation & Self Reflection",
    subtitle: "Measure Each Component",
    coreAddition: "Scorers, Calibrated Judges, Completeness",
    keyInsight: "Deterministic checks first, judges only where nothing exact exists, and never let the model grade its own completeness.",
    layer: "foundations",
    prevVersion: "f08",
  },
  f10: {
    title: "Scheduling & Long Running Tasks",
    subtitle: "Work That Outlives a Request",
    coreAddition: "Leases, Heartbeats & Fencing Tokens",
    keyInsight: "Checkpoint every batch and fence every write, so a crashed job resumes and a zombie worker can't overwrite it.",
    layer: "foundations",
    prevVersion: "f09",
  },
  f11: {
    title: "Async Execution",
    subtitle: "Many Calls, All Bounded",
    coreAddition: "Semaphores, Timeouts & Structured Cancellation",
    keyInsight: "Cap what's in flight, time every call, cancel children with their caller, and never block the event loop.",
    layer: "foundations",
    prevVersion: "f10",
  },
  f12: {
    title: "Event Driven Agents",
    subtitle: "Agents Triggered by Change",
    coreAddition: "Transactional Outbox & Idempotent Consumers",
    keyInsight: "Write the event with the change, deliver at least once, and make handling it twice harmless.",
    layer: "foundations",
    prevVersion: "f11",
  },
  f13: {
    title: "State Machines",
    subtitle: "Declared Edges, Guarded Moves",
    coreAddition: "Per-key Merge Reducers",
    keyInsight: "Most 'the model ignored the history' bugs are merge bugs: one parallel branch overwrote another's data.",
    layer: "foundations",
    prevVersion: "f12",
  },
  f14: {
    title: "Error Handling",
    subtitle: "Sort Errors by What to Do",
    coreAddition: "Taxonomy, Fallbacks & Circuit Breakers",
    keyInsight: "Retry transient errors, stop on permanent and policy ones, and never cache a fallback under the real key.",
    layer: "foundations",
    prevVersion: "f13",
  },
  f15: {
    title: "Persistence",
    subtitle: "Runs That Survive Restarts",
    coreAddition: "Versioned Checkpoints & Migrations",
    keyInsight: "Checkpoint every step, migrate old schemas on read, and refuse checkpoints written by newer code.",
    layer: "foundations",
    prevVersion: "f14",
  },

  // Part 2: Production Patterns
  p16: {
    title: "Retry Patterns",
    subtitle: "Retrying Without Making It Worse",
    coreAddition: "Full Jitter, Retry-After & Retry Budgets",
    keyInsight: "Only transient errors, spread in time, within the deadline, capped as a share of traffic, with one idempotency key.",
    layer: "patterns",
    prevVersion: "f15",
  },
  p17: {
    title: "Background Workers",
    subtitle: "Slow Work Off the Request Path",
    coreAddition: "Visibility Timeouts, DLQ & Single-flight",
    keyInsight: "Three uploads of one document should cost one run plus at most one follow-up, never three racing runs.",
    layer: "patterns",
    prevVersion: "p16",
  },
  p18: {
    title: "Redis Checkpointing",
    subtitle: "Hot State and Locks",
    coreAddition: "Token-owned SET NX PX Locks",
    keyInsight: "Build every key in one place, expire everything, and release only the lock you own.",
    layer: "patterns",
    prevVersion: "p17",
  },
  p19: {
    title: "PostgreSQL State Store",
    subtitle: "The System of Record",
    coreAddition: "Optimistic Concurrency & SKIP LOCKED",
    keyInsight: "Write only if the version you read hasn't changed, and let the database hand each job to exactly one worker.",
    layer: "patterns",
    prevVersion: "p18",
  },
  p20: {
    title: "Queues",
    subtitle: "Order per Key, Parallel Across Keys",
    coreAddition: "Partitioned Log & Consumer Groups",
    keyInsight: "Commit offsets after processing, deduplicate in the consumer, and dead-letter what keeps failing.",
    layer: "patterns",
    prevVersion: "p19",
  },
  p21: {
    title: "Streaming",
    subtitle: "First Words Fast",
    coreAddition: "SSE Frames, Partial JSON & Real Cancellation",
    keyInsight: "Stream the answer field as it arrives, validate at the end with a retract path, and stop the model when the user leaves.",
    layer: "patterns",
    prevVersion: "p20",
  },
  p22: {
    title: "Observability",
    subtitle: "One Trace per Run",
    coreAddition: "GenAI Spans, Cost & Redacted Sampling",
    keyInsight: "Trace every step, price every call, store IDs not text, and turn sampled traces into eval cases.",
    layer: "patterns",
    prevVersion: "p21",
  },
  p23: {
    title: "Docker",
    subtitle: "Package What You Tested",
    coreAddition: "Multi-stage, Non-root & Checksummed Artifacts",
    keyInsight: "Liveness and readiness are different questions: don't report ready until the index checks out.",
    layer: "patterns",
    prevVersion: "p22",
  },
  p24: {
    title: "Deployment",
    subtitle: "Prompts and Models Are Releases",
    coreAddition: "Eval Gate, Shadow & Sticky Canary",
    keyInsight: "Prove it offline, watch it in shadow, give it to a few, and keep the stable release running for instant rollback.",
    layer: "patterns",
    prevVersion: "p23",
  },
  p25: {
    title: "Configuration",
    subtitle: "Typed, Validated, Secret-safe",
    coreAddition: "Fail-fast Settings & Audited Flags",
    keyInsight: "Parse configuration once, refuse it loudly, never print a secret, and ship prompt versions as feature flags.",
    layer: "patterns",
    prevVersion: "p24",
  },
  p26: {
    title: "Monitoring",
    subtitle: "Service Health and Answer Health",
    coreAddition: "Burn-rate Alerts & Quality Signals",
    keyInsight: "Alert on what users feel, at the speed it matters, once per incident: SLO burn rates plus schema, fallback and retrieval rates.",
    layer: "patterns",
    prevVersion: "p25",
  },
  p27: {
    title: "Evaluation Pipelines",
    subtitle: "Evaluation as a Release Gate",
    coreAddition: "Cached Outputs & Paired CI Gate",
    keyInsight: "Same examples, same settings, honest confidence intervals, and a gate that checks every slice, not just the average.",
    layer: "patterns",
    prevVersion: "p26",
  },
  p28: {
    title: "Cost Tracking",
    subtitle: "Price Every Call",
    coreAddition: "Ledger, Reservations & Cascades",
    keyInsight: "Reserve the worst case before calling, so budgets stop spending instead of reporting it.",
    layer: "patterns",
    prevVersion: "p27",
  },
  p29: {
    title: "Security",
    subtitle: "Assume the Model Will Be Fooled",
    coreAddition: "Taint-aware Least Privilege",
    keyInsight: "Marking and detection lower the risk; removing side-effecting tools once untrusted content is in context is what holds.",
    layer: "patterns",
    prevVersion: "p28",
  },
  p30: {
    title: "Scaling",
    subtitle: "When the Provider Is the Bottleneck",
    coreAddition: "Rate-aware Gateway, AIMD & Single-flight Cache",
    keyInsight: "Respect request and token limits before calling, batch, collapse duplicate work, and spill over to a second provider.",
    layer: "patterns",
    prevVersion: "p29",
  },

  // Part 3: Complete Systems
  c01: {
    title: "Healthcare Platform",
    subtitle: "Flagship Enterprise Agent Platform",
    coreAddition: "Multi-Service Orchestrator Mesh",
    keyInsight: "The flagship Healthcare system integrates all concepts: FastAPI, Docker Compose, Postgres, Redis, and workers.",
    layer: "systems",
    prevVersion: "p30",
  },
  c02: {
    title: "Customer Support",
    subtitle: "One Conversation Across Channels",
    coreAddition: "Rules-first Routing & Human Hand-off",
    keyInsight: "Deterministic where the path is known, the model where it isn't, a person where it matters, and never two voices at once.",
    layer: "systems",
    prevVersion: "c01",
  },
  c03: {
    title: "Research Assistant",
    subtitle: "Agent + RAG + Harness Together",
    coreAddition: "Verified Sub-agent Findings",
    keyInsight: "Every sentence in the report traces to a source, and every gap is stated instead of invented.",
    layer: "systems",
    prevVersion: "c02",
  },
  c04: {
    title: "Coding Agent",
    subtitle: "A Sandbox It Can't Escape or Cheat",
    coreAddition: "Read-only Tests & Scrubbed Execution",
    keyInsight: "Let it try anything inside the box, make sure the box holds, and have a person merge the result.",
    layer: "systems",
    prevVersion: "c03",
  },

  // Part 4: Infrastructure
  i01: {
    title: "Local Compose Deployment",
    subtitle: "Complete Multi-Container Environment",
    coreAddition: "Unified Docker Compose Spec",
    keyInsight: "Local infrastructure orchestrates Redis cache, Postgres DB, FastAPI app, and worker tasks seamlessly.",
    layer: "infrastructure",
    prevVersion: "c04",
  },
};

export const LAYERS = [
  {
    id: "foundations" as const,
    label: "Part 1: Foundations",
    color: "#3B82F6",
    versions: ["f01", "f02", "f03", "f04", "f05", "f06", "f07", "f08", "f09", "f10", "f11", "f12", "f13", "f14", "f15"],
  },
  {
    id: "patterns" as const,
    label: "Part 2: Production Patterns",
    color: "#10B981",
    versions: ["p16", "p17", "p18", "p19", "p20", "p21", "p22", "p23", "p24", "p25", "p26", "p27", "p28", "p29", "p30"],
  },
  {
    id: "systems" as const,
    label: "Part 3: Complete Systems",
    color: "#8B5CF6",
    versions: ["c01", "c02", "c03", "c04"],
  },
  {
    id: "infrastructure" as const,
    label: "Part 4: Infrastructure",
    color: "#F59E0B",
    versions: ["i01"],
  },
] as const;
