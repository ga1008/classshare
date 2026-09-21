/** Resolve every legacy island controller against the document's pinned graph. */
export function legacyModuleUrl(path: string): string {
  const revision = window.__LS_ASSET_REV || 'dev';
  return /^[a-f0-9]{64}$/.test(revision)
    ? `/static/assets/${revision}/js/${path}`
    : `/static/js/${path}?v=${encodeURIComponent(revision)}`;
}
