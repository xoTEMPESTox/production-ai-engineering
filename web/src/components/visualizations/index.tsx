"use client";

import { lazy, Suspense } from "react";
import { useTranslations } from "@/lib/i18n";

const visualizations: Record<
  string,
  React.LazyExoticComponent<React.ComponentType<{ title?: string }>>
> = {
  // Part 1: Foundations
  f01: lazy(() => import("./s01-agent-loop")),
  f02: lazy(() => import("./s02-tool-dispatch")),
  f03: lazy(() => import("./s03-permission")),
  f04: lazy(() => import("./s03-todo-write")),
  f05: lazy(() => import("./s09-memory")),
  f06: lazy(() => import("./s07-task-system")),
  f07: lazy(() => import("./s09-agent-teams")),
  f08: lazy(() => import("./s03-permission")),
  f09: lazy(() => import("./s11-error-recovery")),
  f10: lazy(() => import("./s14-cron-scheduler")),
  f11: lazy(() => import("./s08-background-tasks")),
  f12: lazy(() => import("./s10-team-protocols")),
  f13: lazy(() => import("./s20-comprehensive")),
  f14: lazy(() => import("./s11-error-recovery")),
  f15: lazy(() => import("./s09-memory")),

  // Part 2: Production Patterns
  p16: lazy(() => import("./s11-error-recovery")),
  p17: lazy(() => import("./s08-background-tasks")),
  p18: lazy(() => import("./s06-context-compact")),
  p19: lazy(() => import("./s09-memory")),
  p20: lazy(() => import("./s08-background-tasks")),
  p21: lazy(() => import("./s02-tool-dispatch")),
  p22: lazy(() => import("./s19-mcp-tools")),
  p23: lazy(() => import("./s12-worktree-task-isolation")),
  p24: lazy(() => import("./s20-comprehensive")),
  p25: lazy(() => import("./s10-system-prompt")),
  p26: lazy(() => import("./s20-comprehensive")),
  p27: lazy(() => import("./s11-error-recovery")),
  p28: lazy(() => import("./s20-comprehensive")),
  p29: lazy(() => import("./s03-permission")),
  p30: lazy(() => import("./s20-comprehensive")),

  // Part 3: Complete Systems
  c01: lazy(() => import("./s20-comprehensive")),
  c02: lazy(() => import("./s20-comprehensive")),
  c03: lazy(() => import("./s20-comprehensive")),
  c04: lazy(() => import("./s20-comprehensive")),

  // Part 4: Infrastructure
  i01: lazy(() => import("./s20-comprehensive")),
};

export function SessionVisualization({ version }: { version: string }) {
  const t = useTranslations("viz");
  const Component = visualizations[version];
  if (!Component) return null;
  return (
    <Suspense
      fallback={
        <div className="min-h-[500px] animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-800" />
      }
    >
      <div className="min-h-[500px]">
        <Component title={t(version)} />
      </div>
    </Suspense>
  );
}
