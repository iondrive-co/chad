import { useState, useEffect, useRef } from "react";
import type { ChadAPI, Account } from "chad-client";

interface Props {
  api: ChadAPI;
  selected: Account | null;
  onSelect: (account: Account | null) => void;
}

export function AccountPicker({ api, selected, onSelect }: Props) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const didAutoSelect = useRef(false);

  // Fetch accounts once on mount
  useEffect(() => {
    api
      .listAccounts()
      .then((r) => {
        setAccounts(r.accounts);
        // Auto-select: prefer CODING role, then first ready account
        if (!didAutoSelect.current && r.accounts.length > 0) {
          didAutoSelect.current = true;
          const coding = r.accounts.find((a) => a.ready && a.role === "CODING");
          const ready = coding ?? r.accounts.find((a) => a.ready);
          if (ready) onSelect(ready);
        }
      })
      .catch(() => setAccounts([]));
  }, [api]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <select
      value={selected?.name ?? ""}
      onChange={(e) => {
        const acct = accounts.find((a) => a.name === e.target.value) ?? null;
        onSelect(acct);
      }}
    >
      <option value="" disabled>
        Select an account...
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
