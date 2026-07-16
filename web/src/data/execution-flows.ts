import type { FlowNode, FlowEdge } from "@/types/agent-data";

export interface FlowDefinition {
  nodes: FlowNode[];
  edges: FlowEdge[];
}

const FLOW_WIDTH = 600;
const COL_CENTER = FLOW_WIDTH / 2;
const COL_LEFT = 140;
const COL_RIGHT = FLOW_WIDTH - 140;

export const EXECUTION_FLOWS: Record<string, FlowDefinition> = {
  f01: {
    nodes: [
      { id: "start", label: "State Update Call", type: "start", x: COL_CENTER, y: 30 },
      { id: "lock", label: "Acquire Thread Lock", type: "process", x: COL_CENTER, y: 110 },
      { id: "copy", label: "Deep Copy Current State", type: "process", x: COL_CENTER, y: 190 },
      { id: "validate", label: "Validate Fields", type: "decision", x: COL_CENTER, y: 280 },
      { id: "commit", label: "Increment Version & Log History", type: "process", x: COL_CENTER, y: 380 },
      { id: "end", label: "Return Immutable State", type: "end", x: COL_CENTER, y: 470 },
    ],
    edges: [
      { from: "start", to: "lock" },
      { from: "lock", to: "copy" },
      { from: "copy", to: "validate" },
      { from: "validate", to: "commit", label: "ok" },
      { from: "commit", to: "end" },
    ],
  },
  f02: {
    nodes: [
      { id: "start", label: "User Input", type: "start", x: COL_CENTER, y: 30 },
      { id: "llm", label: "LLM Call", type: "process", x: COL_CENTER, y: 110 },
      { id: "tool_check", label: "tool_use?", type: "decision", x: COL_CENTER, y: 190 },
      { id: "dispatch", label: "Execute Tool", type: "subprocess", x: COL_LEFT, y: 280 },
      { id: "append", label: "Append Result to State", type: "process", x: COL_LEFT, y: 360 },
      { id: "end", label: "Final Output", type: "end", x: COL_RIGHT, y: 280 },
    ],
    edges: [
      { from: "start", to: "llm" },
      { from: "llm", to: "tool_check" },
      { from: "tool_check", to: "dispatch", label: "yes" },
      { from: "tool_check", to: "end", label: "no" },
      { from: "dispatch", to: "append" },
      { from: "append", to: "llm" },
    ],
  },
  f03: {
    nodes: [
      { id: "start", label: "Tool Invocations", type: "start", x: COL_CENTER, y: 30 },
      { id: "schema", label: "Schema Validation", type: "decision", x: COL_CENTER, y: 120 },
      { id: "dispatch", label: "Map & Run Tool Handler", type: "subprocess", x: COL_LEFT, y: 220 },
      { id: "error", label: "Format Error Output", type: "process", x: COL_RIGHT, y: 220 },
      { id: "end", label: "Return Tool Result", type: "end", x: COL_CENTER, y: 330 },
    ],
    edges: [
      { from: "start", to: "schema" },
      { from: "schema", to: "dispatch", label: "valid" },
      { from: "schema", to: "error", label: "invalid" },
      { from: "dispatch", to: "end" },
      { from: "error", to: "end" },
    ],
  },
  f04: {
    nodes: [
      { id: "start", label: "LLM Response", type: "start", x: COL_CENTER, y: 30 },
      { id: "parse", label: "JSON Parse Output", type: "process", x: COL_CENTER, y: 110 },
      { id: "validate", label: "Pydantic Validator", type: "decision", x: COL_CENTER, y: 200 },
      { id: "fail", label: "Format Error to Prompt", type: "process", x: COL_LEFT, y: 300 },
      { id: "end", label: "Return Typed Object", type: "end", x: COL_RIGHT, y: 300 },
    ],
    edges: [
      { from: "start", to: "parse" },
      { from: "parse", to: "validate" },
      { from: "validate", to: "end", label: "valid" },
      { from: "validate", to: "fail", label: "invalid" },
      { from: "fail", to: "start", label: "retry" },
    ],
  },
  f05: {
    nodes: [
      { id: "start", label: "Context Threshold", type: "start", x: COL_CENTER, y: 30 },
      { id: "summary", label: "Summarize History", type: "process", x: COL_CENTER, y: 120 },
      { id: "semantic", label: "Store Semantic Memory", type: "subprocess", x: COL_LEFT, y: 220 },
      { id: "end", label: "Compacted Prompt Context", type: "end", x: COL_RIGHT, y: 220 },
    ],
    edges: [
      { from: "start", to: "summary" },
      { from: "summary", to: "semantic" },
      { from: "summary", to: "end" },
      { from: "semantic", to: "end" },
    ],
  },
  f06: {
    nodes: [
      { id: "start", label: "Complex Goal", type: "start", x: COL_CENTER, y: 30 },
      { id: "decompose", label: "Decompose into Steps", type: "process", x: COL_CENTER, y: 110 },
      { id: "dag", label: "Create Task DAG", type: "process", x: COL_CENTER, y: 195 },
      { id: "loop", label: "Execute Next Step", type: "subprocess", x: COL_LEFT, y: 290 },
      { id: "check", label: "DAG complete?", type: "decision", x: COL_CENTER, y: 380 },
      { id: "end", label: "Goal Reached", type: "end", x: COL_RIGHT, y: 380 },
    ],
    edges: [
      { from: "start", to: "decompose" },
      { from: "decompose", to: "dag" },
      { from: "dag", to: "loop" },
      { from: "loop", to: "check" },
      { from: "check", to: "loop", label: "no" },
      { from: "check", to: "end", label: "yes" },
    ],
  },
  f07: {
    nodes: [
      { id: "start", label: "Orchestrator Task", type: "start", x: COL_CENTER, y: 30 },
      { id: "delegate", label: "Delegate to Workers", type: "process", x: COL_CENTER, y: 120 },
      { id: "worker1", label: "Worker Agent 1", type: "subprocess", x: COL_LEFT, y: 220 },
      { id: "worker2", label: "Worker Agent 2", type: "subprocess", x: COL_RIGHT, y: 220 },
      { id: "aggregate", label: "Aggregate Outputs", type: "process", x: COL_CENTER, y: 320 },
      { id: "end", label: "Final Combined Result", type: "end", x: COL_CENTER, y: 410 },
    ],
    edges: [
      { from: "start", to: "delegate" },
      { from: "delegate", to: "worker1" },
      { from: "delegate", to: "worker2" },
      { from: "worker1", to: "aggregate" },
      { from: "worker2", to: "aggregate" },
      { from: "aggregate", to: "end" },
    ],
  },
  f08: {
    nodes: [
      { id: "start", label: "Agent Invocations", type: "start", x: COL_CENTER, y: 30 },
      { id: "run", label: "Collect Trajectory logs", type: "process", x: COL_CENTER, y: 110 },
      { id: "judge", label: "LLM-as-a-Judge Eval", type: "decision", x: COL_CENTER, y: 200 },
      { id: "pass", label: "Store Metrics (Pass)", type: "end", x: COL_LEFT, y: 300 },
      { id: "fail", label: "Raise Assertion (Fail)", type: "end", x: COL_RIGHT, y: 300 },
    ],
    edges: [
      { from: "start", to: "run" },
      { from: "run", to: "judge" },
      { from: "judge", to: "pass", label: "metrics ok" },
      { from: "judge", to: "fail", label: "violations" },
    ],
  },
  f09: {
    nodes: [
      { id: "start", label: "Dangerous Action", type: "start", x: COL_CENTER, y: 30 },
      { id: "gate", label: "Trigger Interruption", type: "process", x: COL_CENTER, y: 110 },
      { id: "approve", label: "Await Human Input", type: "decision", x: COL_CENTER, y: 200 },
      { id: "resume", label: "Resume State Execution", type: "subprocess", x: COL_LEFT, y: 300 },
      { id: "cancel", label: "Abort/Rollback state", type: "end", x: COL_RIGHT, y: 300 },
    ],
    edges: [
      { from: "start", to: "gate" },
      { from: "gate", to: "approve" },
      { from: "approve", to: "resume", label: "approved" },
      { from: "approve", to: "cancel", label: "rejected" },
    ],
  },
  f10: {
    nodes: [
      { id: "start", label: "Cron Definition", type: "start", x: COL_CENTER, y: 30 },
      { id: "store", label: "Register Task Schedule", type: "process", x: COL_CENTER, y: 120 },
      { id: "loop", label: "Scheduler Heartbeat", type: "process", x: COL_CENTER, y: 210 },
      { id: "check", label: "Is execution due?", type: "decision", x: COL_CENTER, y: 300 },
      { id: "run", label: "Trigger Agent Worker", type: "subprocess", x: COL_LEFT, y: 400 },
    ],
    edges: [
      { from: "start", to: "store" },
      { from: "store", to: "loop" },
      { from: "loop", to: "check" },
      { from: "check", to: "run", label: "yes" },
      { from: "check", to: "loop", label: "no" },
      { from: "run", to: "loop" },
    ],
  },
};

export function getFlowForVersion(version: string): FlowDefinition | null {
  return EXECUTION_FLOWS[version] || null;
}
