"use client";

import { useEffect, useState } from "react";
import { AgentForm } from "@/components/AgentForm";
import { ErrorBox, Loading, PageHeader } from "@/components/ui";
import { api, type AgentConfig } from "@/lib/api";

export default function EditAgentPage({ params }: { params: { handle: string } }) {
  const [agent, setAgent] = useState<AgentConfig | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<AgentConfig>(`/v1/portal/agents/${params.handle}`).then(setAgent).catch((e) => setError((e as Error).message));
  }, [params.handle]);
  return (
    <>
      <PageHeader title={`Edit @${params.handle}`} back subtitle="Everything can change except the company name and the agent name." />
      {error && <ErrorBox message={error} />}
      {!agent && !error && <Loading />}
      {agent && <AgentForm existing={agent} />}
    </>
  );
}
