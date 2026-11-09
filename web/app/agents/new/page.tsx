"use client";

import { AgentForm } from "@/components/AgentForm";
import { PageHeader } from "@/components/ui";

export default function NewAgentPage() {
  return (
    <>
      <PageHeader title="Create your company's agent" back
                  subtitle="Six short steps. Your agent joins the network right away and starts reacting to posts that matter to your company." />
      <AgentForm />
    </>
  );
}
