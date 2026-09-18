export default function PnLSparkline({ series }: { series: number[] }) {
  if (series.length === 0) return <span style={{ color: "#999" }}>no data</span>;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  const w = 100;
  const h = 24;
  const step = w / Math.max(series.length - 1, 1);
  const points = series
    .map((v, i) => `${i * step},${h - ((v - min) / range) * h}`)
    .join(" ");
  return (
    <svg width={w} height={h}>
      <polyline points={points} fill="none" stroke="#111" strokeWidth={1.5} />
    </svg>
  );
}
