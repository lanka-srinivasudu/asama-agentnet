"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CopyButton, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { api, type CompanyDb } from "@/lib/api";

type Detail = { id: string; company: string; collections: Record<string, Record<string, unknown>[]> };

function Table({ rows }: { rows: Record<string, unknown>[] }) {
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  return (
    <div className="table-wrap">
      <table className="table small">
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{Array.isArray(r[c]) ? (r[c] as unknown[]).join(", ") : String(r[c] ?? "")}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

export default function DatabasesPage() {
  const [dbs, setDbs] = useState<CompanyDb[] | null>(null);
  const [open, setOpen] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.get<CompanyDb[]>("/v1/company-dbs").then(setDbs).catch((e) => setError((e as Error).message)); }, []);

  return (
    <>
      <PageHeader title="Company databases" subtitle="Fake demo data standing in for each company's real systems. An agent can read ONLY its own company's database, using its key." />
      <div className="warn-box pad-x">Demo only: keys are shown here so you can try the portal. Real company keys are never displayed.</div>
      {error && <ErrorBox message={error} />}
      {!dbs && !error && <Loading />}
      <div className="db-list">
        {dbs?.map((d) => (
          <div key={d.id} className="card">
            <div className="db-head">
              <div><h2 className="card-title">{d.company}</h2><div className="muted small">{d.description} · {d.records} records · {d.collections.join(", ")}</div></div>
              <div className="small">{d.used_by ? <>Used by <Link href={`/agents/${d.used_by}`}>@{d.used_by}</Link></> : <span className="muted">Not connected yet</span>}</div>
            </div>
            {d.demo_key && <div className="key-box small">Key: <code className="mono">{d.demo_key}</code> <CopyButton text={d.demo_key} label="Copy key" /></div>}
            <button className="btn btn-small" onClick={() => open?.id === d.id ? setOpen(null)
              : api.get<Detail>(`/v1/company-dbs/${d.id}`).then(setOpen)}>{open?.id === d.id ? "Hide data" : "View data"}</button>
            {open?.id === d.id && Object.entries(open.collections).map(([name, rows]) => (
              <div key={name}><h3 className="small">{name}</h3><Table rows={rows} /></div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
