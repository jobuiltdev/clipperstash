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

  return <StreamerSessions streamerId={id} />;
}
