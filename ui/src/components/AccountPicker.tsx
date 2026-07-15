import { useState, useEffect, useRef } from "react";
import type { ChadAPI, Account } from "chad-client";

interface Props {
  api: ChadAPI;
  selected: Account | null;
  onSelect: (account: Account | null) => void;
  /** Whether the picker is disabled */
  disabled?: boolean;
  /** Placeholder text for the empty option */
  placeholder?: string;
  /** Whether to allow "None" option (not auto-selecting) */
  allowNone?: boolean;
  /**
   * Whether to auto-select a default account on mount. Off when the parent
   * already drives the selection (e.g. the coding agent, which is seeded from
   * the session's own agent) so this picker can't clobber that choice.
   */
  autoSelect?: boolean;
}

export function AccountPicker({
  api,
  selected,
  onSelect,
  disabled = false,
  placeholder = "Select an account...",
  allowNone = false,
  autoSelect = true,
}: Props) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const didAutoSelect = useRef(false);

  // Fetch accounts once on mount
  useEffect(() => {
    api
      .listAccounts()
      .then((r) => {
        setAccounts(r.accounts);
        // Auto-select: prefer CODING role, then first ready account
        // Skip when allowNone (optional pickers) or when the parent owns the selection.
        if (autoSelect && !allowNone && !didAutoSelect.current && r.accounts.length > 0) {
          didAutoSelect.current = true;
          const coding = r.accounts.find((a) => a.ready && a.role === "CODING");
          const ready = coding ?? r.accounts.find((a) => a.ready);
          if (ready) onSelect(ready);
        }
      })
      .catch(() => setAccounts([]));
  }, [api, allowNone, autoSelect]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <select
      value={selected?.name ?? ""}
      disabled={disabled}
      onChange={(e) => {
        const acct = accounts.find((a) => a.name === e.target.value) ?? null;
        onSelect(acct);
      }}
    >
      <option value="" disabled={!allowNone}>
        {placeholder}
      </option>
      {accounts.map((a) => (
        <option key={a.name} value={a.name} disabled={!a.ready}>
          {a.name} ({a.provider}
          {a.model ? ` / ${a.model}` : ""}
          {!a.ready ? " - not ready" : ""})
        </option>
      ))}
    </select>
  );
}
