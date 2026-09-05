import { notFound } from "next/navigation";

import { MomentDetail } from "@/components/dashboard/moment-detail";
import { readId } from "@/lib/route-params";

export default async function MomentPage({
  params,
}: {
  params: Promise<{ momentId: string }>;
}) {
  const { momentId } = await params;
  const id = readId(momentId);
  if (id === null) {
    notFound();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-semibold tracking-tight">Detected moment</h1>
      <MomentDetail momentId={id} />
    </div>
  );
}
