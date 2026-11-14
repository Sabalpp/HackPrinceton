import { ConnectionStatus } from "@/components/connection-status";

// Placeholder home page. Saksham owns the dashboard UI (see docs/agents/SAKSHAM.md).
export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 px-6">
      <div>
        <h1 className="text-4xl font-semibold tracking-tight">FarCare</h1>
        <p className="mt-2 text-lg text-zinc-600 dark:text-zinc-400">Far away. Always there.</p>
      </div>
      <ConnectionStatus />
      <p className="text-xs text-zinc-500">
        Hackathon prototype. Not a medical device. Demo data is synthetic and labeled simulated.
      </p>
    </main>
  );
}
