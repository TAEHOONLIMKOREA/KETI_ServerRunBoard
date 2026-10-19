import { WARN_BADGES } from "../format";

export function Badge({ text, kind }: { text: string; kind?: "warn" | "type" }) {
  const cls = kind ?? (WARN_BADGES.has(text) ? "warn" : "");
  return <span className={`badge ${cls}`}>{text}</span>;
}

export function Badges({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <>
      {items.map((b) => (
        <Badge key={b} text={b} />
      ))}
    </>
  );
}
