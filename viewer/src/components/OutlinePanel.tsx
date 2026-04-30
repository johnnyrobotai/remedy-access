import type { OutlineNode } from "../lib/api";

export function OutlinePanel({
  outline,
  onJump,
}: {
  outline: OutlineNode[];
  onJump?: () => void;
}) {
  if (outline.length === 0) {
    return <p className="empty-state">No headings detected.</p>;
  }

  return (
    <nav aria-label="Document outline">
      <ol className="outline-list">
        {outline.map((n) => (
          <li key={n.id} style={{ marginInlineStart: `${(n.level - 1) * 0.75}rem` }}>
            <a
              href={`#${n.id}`}
              onClick={(e) => {
                e.preventDefault();
                const target = document.getElementById(n.id);
                if (target) {
                  onJump?.();
                  target.scrollIntoView({ behavior: "smooth", block: "start" });
                  (target as HTMLElement).focus({ preventScroll: true });
                }
              }}
            >
              {n.text}
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}
