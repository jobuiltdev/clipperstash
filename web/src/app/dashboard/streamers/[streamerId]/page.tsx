import { notFound } from "next/navigation";

import { StreamerSessions } from "@/components/dashboard/streamer-sessions";
import { readId } from "@/lib/route-params";

export default async function StreamerPage({
  params,
}: {
  params: Promise<{ streamerId: string }>;
}) {
  const { streamerId } = await params;
  const id = readId(streamerId);
  if (id === null) {
    notFound();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-semibold tracking-tight">Streamer</h1>
      <StreamerSessions streamerId={id} />
    </div>
  );
}
