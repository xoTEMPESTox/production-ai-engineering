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
    subtitle: "Controlling Execution Cycles",
    coreAddition: "Autonomous Execution Cycle",
    keyInsight: "The core loop coordinates calling LLMs, interpreting tools, and driving progression until completion.",
    layer: "foundations",
    prevVersion: "f01",
  },
  f03: {
    title: "Prompting & Structured Output",
    subtitle: "Enforcing Schema Reliability",
    coreAddition: "Pydantic Schema Parser",
    keyInsight: "LLMs must output standardized JSON matching validation rules for machine-to-machine reliability.",
    layer: "foundations",
    prevVersion: "f02",
  },
  f04: {
    title: "Tool Calling",
    subtitle: "Interacting with the World",
    coreAddition: "Schema-Validated Dispatcher",
    keyInsight: "Standardizing tool execution requires explicit type signatures, validation, and a centralized dispatcher.",
    layer: "foundations",
    prevVersion: "f03",
  },
  f05: {
    title: "Memory System",
    subtitle: "Preserving Crucial Context",
    coreAddition: "Semantic Key-Value Memories",
    keyInsight: "An agent needs distinct memory layers: short-term context compaction and durable episodic storage.",
    layer: "foundations",
    prevVersion: "f04",
  },
  f06: {
    title: "Planning & Task Decomposition",
    subtitle: "Strategic Execution Routing",
    coreAddition: "DAG Step Coordinator",
    keyInsight: "Complex goals require a planner to decompose objectives into ordered tasks before execution begins.",
    layer: "foundations",
    prevVersion: "f05",
  },
  f07: {
    title: "Multi-Agent Coordination",
    subtitle: "Collaborative Orchestration",
    coreAddition: "Router & Worker Mesh",
    keyInsight: "Specialized agents coordinate and delegate tasks to each other via structured communication layers.",
    layer: "foundations",
    prevVersion: "f06",
  },
  f08: {
    title: "Human Approval",
    subtitle: "Placing Humans in the Loop",
    coreAddition: "Interactive Approval Gates",
    keyInsight: "High-risk tool invocations must trigger state pause gates awaiting explicit human authorization.",
    layer: "foundations",
    prevVersion: "f07",
  },
  f09: {
    title: "Evaluation & Self Reflection",
    subtitle: "Continuous Performance Verification",
    coreAddition: "LLM-as-a-Judge Pipeline",
    keyInsight: "Production quality demands automated test suites validating outputs against accuracy and safety matrices.",
    layer: "foundations",
    prevVersion: "f08",
  },
  f10: {
    title: "Scheduling & Long Running Tasks",
    subtitle: "Temporal Task Execution",
    coreAddition: "Asynchronous Cron Scheduler",
    keyInsight: "Durable scheduled loops enable agents to run periodic check-ins, monitoring, and time-driven workflows.",
    layer: "foundations",
    prevVersion: "f09",
  },
  f11: {
    title: "Async Execution",
    subtitle: "Non-blocking Concurrency",
    coreAddition: "Python asyncio Loop",
    keyInsight: "Async concurrency lets the agent execute tasks in parallel without blocking main thread execution.",
    layer: "foundations",
    prevVersion: "f10",
  },
  f12: {
    title: "Event Driven Agents",
    subtitle: "Reactive Event Streaming",
    coreAddition: "Centralized Event Bus",
    keyInsight: "Structuring agents around event streams enables decouple execution, real-time logging, and reactivity.",
    layer: "foundations",
    prevVersion: "f11",
  },
  f13: {
    title: "State Machines",
    subtitle: "Strict Transition Boundaries",
    coreAddition: "Deterministic FSM Guard",
    keyInsight: "State machines protect agents from unpredictable drift by forcing execution through defined state boundaries.",
    layer: "foundations",
    prevVersion: "f12",
  },
  f14: {
    title: "Error Handling",
    subtitle: "Self-Healing Fallback Logic",
    coreAddition: "Exception Classification Engine",
    keyInsight: "Classifying errors lets the system recover from transient issues while escalating severe faults.",
    layer: "foundations",
    prevVersion: "f13",
  },
  f15: {
    title: "Persistence",
    subtitle: "Durable Database Serialization",
    coreAddition: "JSON Database Serialization",
    keyInsight: "Serializing state to a persistent database allows agents to live through program restarts.",
    layer: "foundations",
    prevVersion: "f14",
  },

  // Part 2: Production Patterns
  p16: {
    title: "Retry Patterns",
    subtitle: "Handling Flaky LLMs and APIs",
    coreAddition: "Exponential Backoff & Jitter",
    keyInsight: "Transient errors are mitigated by classifying exceptions and executing retries with exponential backoffs.",
    layer: "patterns",
    prevVersion: "f15",
  },
  p17: {
    title: "Background Workers",
    subtitle: "Asynchronous Offloaded Tasks",
    coreAddition: "Distributed Worker Pool",
    keyInsight: "Heavy computation and slow integrations are run in separate background processes using worker threads.",
    layer: "patterns",
    prevVersion: "p16",
  },
  p18: {
    title: "Redis Checkpointing",
    subtitle: "Durable Memory Checkpoints",
    coreAddition: "Redis State Snapshots",
    keyInsight: "Saving states periodically to Redis ensures the agent can resume run progress on crash.",
    layer: "patterns",
    prevVersion: "p17",
  },
  p19: {
    title: "PostgreSQL State Store",
    subtitle: "ACID State Transactions",
    coreAddition: "DB Ledger Persistence",
    keyInsight: "PostgreSQL ensures robust transaction boundaries, audit logs, and complex state queries.",
    layer: "patterns",
    prevVersion: "p18",
  },
  p20: {
    title: "Queues",
    subtitle: "Backpressure & Load Balancing",
    coreAddition: "Broker Task Queue",
    keyInsight: "Decoupling execution through queues guarantees reliability, backpressure management, and horizontal scalability.",
    layer: "patterns",
    prevVersion: "p19",
  },
  p21: {
    title: "Streaming",
    subtitle: "SSE Token Streaming",
    coreAddition: "Server-Sent Events Streams",
    keyInsight: "Streaming outputs token-by-token improves perceived responsiveness for users significantly.",
    layer: "patterns",
    prevVersion: "p20",
  },
  p22: {
    title: "Observability",
    subtitle: "OpenTelemetry Spans & Traces",
    coreAddition: "OTel Instrumentations",
    keyInsight: "Production observability requires visual call-stacks showing prompts, tokens, and latency.",
    layer: "patterns",
    prevVersion: "p21",
  },
  p23: {
    title: "Docker",
    subtitle: "Containerizing the Runtime",
    coreAddition: "Multi-stage Dockerfile",
    keyInsight: "Docker containerization guarantees environment parity between development workspace and production servers.",
    layer: "patterns",
    prevVersion: "p22",
  },
  p24: {
    title: "Deployment",
    subtitle: "Scaling Cloud Services",
    coreAddition: "Cloud Deployment Manifests",
    keyInsight: "Deploying agents as scalable microservices requires stateless web instances and separate state backends.",
    layer: "patterns",
    prevVersion: "p23",
  },
  p25: {
    title: "Configuration",
    subtitle: "Dynamic Environment Settings",
    coreAddition: "Strong-typed Settings Parser",
    keyInsight: "Separate configuration from logic using environment variables and secure key managers.",
    layer: "patterns",
    prevVersion: "p24",
  },
  p26: {
    title: "Monitoring",
    subtitle: "Real-time Metrics Dashboard",
    coreAddition: "Prometheus Metrics Registry",
    keyInsight: "Dashboards track prompt cost, LLM token usages, and error rates to prevent overruns.",
    layer: "patterns",
    prevVersion: "p25",
  },
  p27: {
    title: "Evaluation Pipelines",
    subtitle: "Automated Regression Testing",
    coreAddition: "Continuous Eval Harness",
    keyInsight: "Automated regression pipelines run tests against datasets to identify drop-offs in prompt performance.",
    layer: "patterns",
    prevVersion: "p26",
  },
  p28: {
    title: "Cost Tracking",
    subtitle: "Financial Governance",
    coreAddition: "Token Cost Aggregator",
    keyInsight: "Token and API pricing trackers keep runs within budget constraints and block abusive sessions.",
    layer: "patterns",
    prevVersion: "p27",
  },
  p29: {
    title: "Security",
    subtitle: "Prompt Injection & Shell Safety",
    coreAddition: "Validation Gates & Sandboxes",
    keyInsight: "Secure agents isolate execution in sandboxed subshells and inspect inputs for malicious injection.",
    layer: "patterns",
    prevVersion: "p28",
  },
  p30: {
    title: "Scaling",
    subtitle: "Handling High Concurrency",
    coreAddition: "Horizontal Scaler Setup",
    keyInsight: "Horizontal scaling is achieved by separating orchestration web servers from background worker nodes.",
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
    subtitle: "Durable Conversational Support System",
    coreAddition: "Omnichannel Router & Persisted Memory",
    keyInsight: "A support agent system coordinates memory, plan continuation, and human escalations.",
    layer: "systems",
    prevVersion: "c01",
  },
  c03: {
    title: "Research Assistant",
    subtitle: "Autonomous Deep Document Search",
    coreAddition: "DAG Document Pipeline",
    keyInsight: "Decomposes complex research goals into parallel retrieval, synthesis, and fact-checking subtasks.",
    layer: "systems",
    prevVersion: "c02",
  },
  c04: {
    title: "Coding Agent",
    subtitle: "Isolated Code Execution & Refactoring",
    coreAddition: "Isolated Worktree Execution Sandbox",
    keyInsight: "Coding agents run safe bash commands and verify refactoring commits in clean workspace environments.",
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
