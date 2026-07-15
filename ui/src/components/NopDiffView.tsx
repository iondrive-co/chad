import { useEffect, useRef } from "react";
import type { FileDiff } from "chad-client";
// Side-effect import: registers the <nop-diff> custom element and defines
// window.NopDiff. The bundle is nop's diff/syntax-highlight core transpiled to
// JS (built from ~/nop/web-diff via `./gradlew :web-diff:jsBrowserDistribution`).
// It is CSP-safe (no eval/Function) and renders inside a Shadow DOM that reads
// chad's CSS variables (--bg, --text, --font-mono, --diff-add-bg, --diff-delete-bg).
import "../vendor/nop-diff.js";

type Theme = "auto" | "light" | "dark";

declare global {
  interface Window {
    NopDiff?: {
      renderStructured: (target: HTMLElement, files: FileDiff[], theme: Theme) => void;
    };
  }
}

/**
 * Side-by-side diff viewer with per-language syntax highlighting and inline
 * word-level diffs. Consumes chad's FileDiff[] directly (no transformation).
 */
export function NopDiffView({ files, theme = "auto" }: { files: FileDiff[]; theme?: Theme }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current && window.NopDiff) {
      window.NopDiff.renderStructured(ref.current, files, theme);
    }
  }, [files, theme]);

  if (!files || files.length === 0) {
    return <p className="no-changes">No changes to display.</p>;
  }

  return <div ref={ref} className="nop-diff-host" />;
}
