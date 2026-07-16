import { cn } from "@/lib/utils";

const LAYER_COLORS = {
  foundations:
    "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  patterns:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300",
  systems:
    "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300",
} as const;

interface BadgeProps {
  layer: keyof typeof LAYER_COLORS;
  children: React.ReactNode;
  className?: string;
}

export function LayerBadge({ layer, children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        LAYER_COLORS[layer],
        className
      )}
    >
      {children}
    </span>
  );
}
