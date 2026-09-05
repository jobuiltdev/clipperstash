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

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-semibold tracking-tight">Stream session</h1>
      <SessionDetail sessionId={id} />
    </div>
  );
}
