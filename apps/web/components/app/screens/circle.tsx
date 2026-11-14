"use client";

import { Info, Users } from "lucide-react";
import { api } from "@/lib/api";
import { useLiveQuery } from "@/lib/dashboard";
import { Ready } from "../shell";
import { EmptyBlock, ErrorBlock, LoadingBlock, PageHeader } from "../kit";
import { MemberCard } from "../people";

const fetchCircle = (token: string, parentId: string) => api.careCircle(token, parentId);

export function CircleScreen() {
  return <Ready>{(r) => <Circle name={r.overview.parent.display_name} />}</Ready>;
}

function Circle({ name }: { name: string }) {
  const { data, error, loading } = useLiveQuery(fetchCircle);
  const members = [...(data ?? [])].sort((a, b) => a.priority - b.priority);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Care Circle"
        title={`People near ${name}`}
        description={`Neighbors and friends ${name} trusts. FarCare only ever contacts people on this list, and only for the kinds of help they agreed to.`}
      />
      {loading && !data && <LoadingBlock label="Loading Care Circle" rows={2} />}
      {error && !data && <ErrorBlock title="Couldn't load the Care Circle" message={error} />}
      {data && members.length === 0 && (
        <EmptyBlock title="No one in the Care Circle yet" icon={Users}>
          Add a neighbor, friend or relative who lives near {name}.
        </EmptyBlock>
      )}
      <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {members.map((m, i) => (
          <MemberCard key={m.responder_id} member={m} rank={i + 1} detailed />
        ))}
      </ul>
      <p className="flex items-start gap-2 rounded-xl border border-border bg-inset p-4 text-sm text-body">
        <Info size={18} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
        Helpers only receive {name}&apos;s first name, the task, and the time. Each person must text FarCare once before we can
        message them.
      </p>
    </div>
  );
}
