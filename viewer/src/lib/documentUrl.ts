export function normalizeDocumentSrc(value: string): string {
  if (!value) return value;

  try {
    const url = new URL(value, window.location.href);
    url.username = "";
    url.password = "";
    return url.href;
  } catch {
    return value;
  }
}

export function shouldSendCredentials(src: string): boolean {
  try {
    return new URL(src, window.location.href).origin === window.location.origin;
  } catch {
    return false;
  }
}
