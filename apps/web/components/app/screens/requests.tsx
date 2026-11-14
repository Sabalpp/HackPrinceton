"use client";

import { useMemo, useState } from "react";
import { HandHeart } from "lucide-react";
import { api, type CareRequest } from "@/lib/api";
import { useDashboard, useLiveQuery } from "@/lib/dashboard";
import { isRequestOpen, sortRequests } from "@/lib/summary";
import { Ready } from "../shell";
import { EmptyBlock, ErrorBlock, LoadingBlock, PageHeader } from "../kit";
import { RequestCard } from "../request";

const fetchRequests = (token: string, parentId: string) => api.careRequests(token, parentId, 50);
type Filter = "open" | "all" | "done";

export function RequestsScreen() {
  return <Ready>{(r) => <Requests name={r.overview.parent.display_name} />}</Ready>;
}

function Requests({ name }: { name: string }) {
  const { timeline } = useDashboard();
  const { data, error, loading } = useLiveQuery(fetchRequests);
  const [filter, setFilter] = useState<Filter>("all");

  const all = useMemo(() => sortRequests(data ?? []), [data]);
  const counts = { open: all.filter(isRequestOpen).length, done: all.filter((r) => !isRequestOpen(r)).length, all: all.length };
  const list = all.filter((r) => (filter === "all" ? true : filter === "open" ? isRequestOpen(r) : !isRequestOpen(r)));
  const eventsFor = (r: CareRequest) => timeline.filter((e) => e.ref_id === r.request_id);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Help requests"
        title={`Who's helping ${name}`}
        description={`Every request ${name} made by text. Nobody is contacted until ${name} says yes, except during a safety alert.`}
      />

      <div role="tablist" aria-label="Filter requests" className="flex flex-wrap gap-2">
        {(["all", "open", "done"] as const).map((f) => (
          <button
            key={f}
            type="button"
            role="tab"
            aria-selected={filter === f}
            onClick={() => setFilter(f)}
            className={`min-h-10 rounded-full px-4 text-sm font-semibold ${
              filter === f ? "bg-primary text-white" : "border border-border bg-surface text-body hover:border-primary"
            }`}
          >
            {f === "all" ? "All" : f === "open" ? "In progress" : "Finished"} <span className="opacity-75">({counts[f]})</span>
          </button>
        ))}
      </div>

      {loading && !data && <LoadingBlock label="Loading requests" />}
      {error && !data && <ErrorBlock title="Couldn't load requests" message={error} />}
      {data && list.length === 0 && (
        <EmptyBlock title={filter === "open" ? "Nothing in progress" : "No help requests yet"} icon={HandHeart}>
          When {name} texts FarCare for help, the request appears here and updates live as helpers reply.
        </EmptyBlock>
      )}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {list.map((r) => (
          <RequestCard key={r.request_id} request={r} parentName={name} events={eventsFor(r)} quiet={!isRequestOpen(r)} />
        ))}
      </div>
    </div>
  );
}
