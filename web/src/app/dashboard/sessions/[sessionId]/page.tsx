import { notFound } from "next/navigation";

import { SessionDetail } from "@/components/dashboard/session-detail";
import { readId } from "@/lib/route-params";

export default async function SessionPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;
  const id = readId(sessionId);
  if (id === null) {
    notFound();
  }

  return <SessionDetail sessionId={id} />;
}
