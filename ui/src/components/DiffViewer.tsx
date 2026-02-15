import type { FileDiff, DiffHunk } from "chad-client";

interface Props {
  files: FileDiff[];
}

/** Escape HTML special characters for safe rendering. */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

interface LineData {
  lineNo: number | null;
  content: string;
  type: "context" | "delete" | "add" | "empty";
}

function buildSideBySideLines(hunk: DiffHunk): { left: LineData[]; right: LineData[] } {
  const left: LineData[] = [];
  const right: LineData[] = [];

  for (const line of hunk.lines) {
    if (line.type === "context") {
      left.push({ lineNo: line.old_line, content: line.content, type: "context" });
      right.push({ lineNo: line.new_line, content: line.content, type: "context" });
    } else if (line.type === "delete") {
      left.push({ lineNo: line.old_line, content: line.content, type: "delete" });
    } else if (line.type === "add") {
      right.push({ lineNo: line.new_line, content: line.content, type: "add" });
    }
  }

  // Pad shorter side
  const maxLen = Math.max(left.length, right.length);
  while (left.length < maxLen) {
    left.push({ lineNo: null, content: "", type: "empty" });
  }
  while (right.length < maxLen) {
    right.push({ lineNo: null, content: "", type: "empty" });
  }

  return { left, right };
}

function DiffHunkView({ hunk }: { hunk: DiffHunk }) {
  const { left, right } = buildSideBySideLines(hunk);

  return (
    <div className="diff-hunk">
      <div className="diff-comparison">
        {/* Left side (original) */}
        <div className="diff-side diff-side-left">
          <div className="diff-side-header">Original</div>
          {left.map((line, i) => (
            <div key={i} className={`diff-line ${line.type}`}>
              <span className="diff-line-no">{line.lineNo ?? ""}</span>
              <span
                className="diff-line-content"
                dangerouslySetInnerHTML={{ __html: escapeHtml(line.content) }}
              />
            </div>
          ))}
        </div>
        {/* Right side (modified) */}
        <div className="diff-side diff-side-right">
          <div className="diff-side-header">Modified</div>
          {right.map((line, i) => (
            <div key={i} className={`diff-line ${line.type}`}>
              <span className="diff-line-no">{line.lineNo ?? ""}</span>
              <span
                className="diff-line-content"
                dangerouslySetInnerHTML={{ __html: escapeHtml(line.content) }}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function DiffFileView({ file }: { file: FileDiff }) {
  let badge = null;
  if (file.is_new) {
    badge = <span className="badge new-file">(new file)</span>;
  } else if (file.is_deleted) {
    badge = <span className="badge deleted-file">(deleted)</span>;
  } else if (file.is_binary) {
    badge = <span className="badge binary-file">(binary)</span>;
  }

  return (
    <div className="diff-file">
      <div className="diff-file-header">
        {file.new_path} {badge}
      </div>
      {file.is_binary ? (
        <div className="diff-binary">Binary file changed</div>
      ) : (
        file.hunks.map((hunk, i) => <DiffHunkView key={i} hunk={hunk} />)
      )}
    </div>
  );
}

export function DiffViewer({ files }: Props) {
  if (!files || files.length === 0) {
    return <p className="no-changes">No changes to display.</p>;
  }

  return (
    <div className="diff-viewer">
      {files.map((file, i) => (
        <DiffFileView key={i} file={file} />
      ))}
    </div>
  );
}
