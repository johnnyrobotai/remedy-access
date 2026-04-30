/**
 * Access Remedy embed loader.
 *
 * Usage on the host page:
 *   <script async src="https://access.project-remedy.com/docbox.js"></script>
 *
 * Every <a href="*.pdf"> is intercepted; clicking opens a full-viewport
 * overlay containing a sandboxed iframe that renders the accessible
 * transcript viewer from the same origin as this script.
 *
 * The host page is never touched beyond:
 *   - the overlay <div> while it's open
 *   - document.body[inert] while the overlay is open (focus trapped inside)
 */

type CleanupFn = () => void;

const VIEWER_ORIGIN = (() => {
  const el = document.currentScript as HTMLScriptElement | null;
  if (el?.src) return new URL(el.src).origin;
  return window.location.origin;
})();

const DOC_PATTERN = /\.(pdf|docx|xlsx)(\?|$|#)/i;

function isDocAnchor(el: Element | null): el is HTMLAnchorElement {
  if (!el || el.tagName !== "A") return false;
  const a = el as HTMLAnchorElement;
  if (!a.href) return false;
  if (a.dataset.docboxIgnore === "1") return false;
  return DOC_PATTERN.test(a.href);
}

function attachClickHandler(): CleanupFn {
  const onClick = (e: MouseEvent) => {
    // Respect modifier keys so users can still open in a new tab.
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (e.button !== 0) return;
    const target = (e.target as Element | null)?.closest("a");
    if (!isDocAnchor(target)) return;
    e.preventDefault();
    openOverlay(target.href);
  };
  document.addEventListener("click", onClick, { capture: true });
  return () => document.removeEventListener("click", onClick, { capture: true });
}

let activeCleanup: CleanupFn | null = null;

function stripUrlCredentials(docUrl: string): string {
  try {
    const url = new URL(docUrl, window.location.href);
    url.username = "";
    url.password = "";
    return url.href;
  } catch {
    return docUrl;
  }
}

function openOverlay(docUrl: string) {
  if (activeCleanup) activeCleanup();
  const safeDocUrl = stripUrlCredentials(docUrl);

  const backdrop = document.createElement("div");
  backdrop.setAttribute("role", "dialog");
  backdrop.setAttribute("aria-modal", "true");
  backdrop.setAttribute("aria-label", "Accessible document viewer");
  Object.assign(backdrop.style, {
    position: "fixed",
    inset: "0",
    background: "rgba(255, 255, 255, 0.88)",
    zIndex: "2147483646",
    display: "flex",
    flexDirection: "column",
  } as CSSStyleDeclaration);

  const bar = document.createElement("div");
  Object.assign(bar.style, {
    display: "flex",
    justifyContent: "flex-end",
    padding: "6px 10px",
    background: "#ffffff",
    color: "#101014",
    borderBottom: "1px solid #d7d9de",
  } as CSSStyleDeclaration);

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.textContent = "Close ×";
  closeBtn.setAttribute("aria-label", "Close accessible document viewer");
  Object.assign(closeBtn.style, {
    background: "transparent",
    color: "#101014",
    border: "1px solid #101014",
    padding: "4px 10px",
    borderRadius: "4px",
    cursor: "pointer",
    font: "inherit",
  } as CSSStyleDeclaration);
  bar.appendChild(closeBtn);

  const iframe = document.createElement("iframe");
  iframe.src = `${VIEWER_ORIGIN}/viewer?src=${encodeURIComponent(safeDocUrl)}`;
  iframe.setAttribute(
    "sandbox",
    "allow-scripts allow-forms allow-same-origin allow-popups allow-downloads"
  );
  iframe.setAttribute("allow", "microphone; display-capture; autoplay");
  iframe.title = "Accessible transcript of document";
  Object.assign(iframe.style, {
    flex: "1",
    width: "100%",
    border: "0",
    background: "#fff",
  } as CSSStyleDeclaration);

  backdrop.appendChild(bar);
  backdrop.appendChild(iframe);
  document.body.appendChild(backdrop);

  const otherChildren = Array.from(document.body.children).filter((c) => c !== backdrop);
  for (const c of otherChildren) (c as HTMLElement).inert = true;

  const previouslyFocused = document.activeElement as HTMLElement | null;
  closeBtn.focus();

  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      cleanup();
    }
  };
  document.addEventListener("keydown", onKey);

  const cleanup = () => {
    document.removeEventListener("keydown", onKey);
    for (const c of otherChildren) (c as HTMLElement).inert = false;
    backdrop.remove();
    previouslyFocused?.focus?.();
    if (activeCleanup === cleanup) activeCleanup = null;
  };
  closeBtn.addEventListener("click", cleanup);
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) cleanup();
  });

  activeCleanup = cleanup;
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => attachClickHandler(), {
    once: true,
  });
} else {
  attachClickHandler();
}

(window as unknown as { DocBox?: { open: (url: string) => void } }).DocBox = {
  open: openOverlay,
};
