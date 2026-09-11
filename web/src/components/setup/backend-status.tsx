"use client";

import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/status";
import { API_BASE_URL, getHealth } from "@/lib/api";
import { useResource } from "@/lib/use-resource";

/**
 * Whether the API is answering at all.
 *
 * The first thing to check when anything else on the page misbehaves, and the
 * one state that explains every other failure, so it is stated plainly rather
 * than left to be inferred from a stack of broken panels.
 */
export function BackendStatus() {
  const { state, reload, reloading } = useResource(getHealth);

  return (
    <section aria-labelledby="backend-heading">
      <Card>
        <CardHeader
          headingId="backend-heading"
          title="Backend"
          actions={
            state.kind === "loading" ? (
              <Badge tone="neutral" dot>
                Checking
              </Badge>
            ) : state.kind === "ready" ? (
              <Badge tone="live" dot>
                Reachable
              </Badge>
            ) : (
              <Badge tone="failed" dot>
                Unreachable
              </Badge>
            )
          }
        />
        <CardBody className="space-y-3">
          <p role="status" className="text-sm leading-relaxed text-muted">
            {state.kind === "loading" && "Checking the API…"}
            {state.kind === "ready" &&
              `The API is answering (status: ${state.data.status}). Everything else on this page talks to it.`}
            {state.kind === "failed" &&
              "Nothing on this page can load while the API is unreachable. Check that the Django server is running and that the address below is right."}
          </p>

          <p className="break-all font-mono text-xs text-faint">
            GET {API_BASE_URL}/api/health/
          </p>

          <Button size="sm" onClick={reload} disabled={reloading}>
            {reloading ? "Checking…" : "Check again"}
          </Button>
        </CardBody>
      </Card>
    </section>
  );
}
