const COLORS: Record<string, string> = {
  draft: "#888",
  backtest: "#3b82f6",
  paper: "#f59e0b",
  live: "#10b981",
  retired: "#4b5563",
};

export default function StatusBadge({ status }: { status: string }) {
  return (
    <span
      style={{
        backgroundColor: COLORS[status] ?? "#000",
        color: "white",
        padding: "2px 8px",
        borderRadius: 6,
        fontSize: 12,
      }}
    >
      {status}
    </span>
  );
}
