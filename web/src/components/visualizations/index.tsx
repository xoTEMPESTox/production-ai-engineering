"use client";

import { lazy, Suspense } from "react";
import { useTranslations } from "@/lib/i18n";

const visualizations: Record<
  string,
  React.LazyExoticComponent<React.ComponentType<{ title?: string }>>
> = {
  f01: lazy(() => import("./s01-agent-loop")),
  f02: lazy(() => import("./s02-tool-dispatch")),
  f03: lazy(() => import("./s03-permission")),
  f04: lazy(() => import("./s03-todo-write")),
  f05: lazy(() => import("./s09-memory")),
  f06: lazy(() => import("./s07-task-system")),
  f07: lazy(() => import("./s09-agent-teams")),
  f08: lazy(() => import("./s11-error-recovery")),
  f09: lazy(() => import("./s03-permission")),
  f10: lazy(() => import("./s14-cron-scheduler")),

  p01: lazy(() => import("./s11-error-recovery")),
  p02: lazy(() => import("./s06-context-compact")),
  p03: lazy(() => import("./s08-background-tasks")),
  p04: lazy(() => import("./s09-memory")),
  p05: lazy(() => import("./s09-memory")),
  p06: lazy(() => import("./s08-background-tasks")),
  p07: lazy(() => import("./s02-tool-dispatch")),
  p08: lazy(() => import("./s19-mcp-tools")),
  p09: lazy(() => import("./s12-worktree-task-isolation")),
  p10: lazy(() => import("./s20-comprehensive")),
  p11: lazy(() => import("./s11-error-recovery")),

  c01: lazy(() => import("./s20-comprehensive")),
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
