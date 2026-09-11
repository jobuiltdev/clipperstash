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

  return <MomentDetail momentId={id} />;
}
