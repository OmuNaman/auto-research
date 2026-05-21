export function CostTicker({ usd }: { usd: number }) {
  return (
    <div className="flex items-baseline gap-1 font-mono text-sm">
      <span className="text-ink-400">$</span>
      <span className="text-ink-100 tabular-nums">{usd.toFixed(4)}</span>
      <span className="text-ink-400 text-xs">USD</span>
    </div>
  );
}
