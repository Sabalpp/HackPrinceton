import { Suspense } from "react";
import { ThreadView } from "@/components/farcare/thread-view";
import { MOCK_STATE } from "@/lib/mock-state";

// Prerender the known threads; other ids render on first request (Next 16 + cacheComponents).
export async function generateStaticParams() {
  return MOCK_STATE.threads.map((t) => ({ id: String(t.id) }));
}

// params is runtime data: read it inside Suspense so navigation stays instant.
export default function ThreadPage({ params }: PageProps<"/threads/[id]">) {
  return (
    <Suspense fallback={<ThreadSkeleton />}>
      {params.then(({ id }) => (
        <ThreadView id={Number(id)} />
      ))}
    </Suspense>
  );
}

function ThreadSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading thread" className="flex flex-col gap-4">
      <div className="h-6 w-32 animate-pulse rounded-lg bg-border" />
      <div className="h-10 w-72 animate-pulse rounded-lg bg-border" />
      <div className="h-72 animate-pulse rounded-2xl bg-border/60" />
    </div>
  );
}
