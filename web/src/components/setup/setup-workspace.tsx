"use client";

import { useCallback, useState } from "react";

import { BackendStatus } from "@/components/setup/backend-status";
import { StreamerResolver } from "@/components/setup/streamer-resolver";
import { TwitchSetup } from "@/components/setup/twitch-setup";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page";

/**
 * The setup page.
 *
 * It owns one piece of shared knowledge: whether the backend has a Twitch
 * application configured. The connection endpoint cannot report that — from
 * there, a missing client id and an unconnected account look identical — but
 * resolving a channel or checking live status will fail with
 * `twitch_not_configured` when it is missing. So the panels that make those
 * calls report it upward, and the Twitch card shows the environment problem
 * instead of inviting someone to connect an account that cannot be connected.
 *
 * It starts as "configured" rather than "unknown" deliberately: assuming the
 * common case keeps the page quiet on a healthy installation, and the moment
 * the API says otherwise the card corrects itself.
 */
export function SetupWorkspace() {
  const [configured, setConfigured] = useState(true);
  const reportUnconfigured = useCallback(() => setConfigured(false), []);

  return (
    <div className="space-y-8">
      <PageHeader
        title="Setup"
        description="Connect the Twitch account ClipperStash acts as, then resolve the channel you want to watch. Each stage of the pipeline runs when you start it."
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] lg:items-start">
        <div className="space-y-6">
          <TwitchSetup configured={configured} />
          <StreamerResolver onUnconfigured={reportUnconfigured} />
        </div>

        <div className="space-y-6">
          <BackendStatus />

          <section aria-labelledby="workflow-heading">
            <Card>
              <CardHeader
                headingId="workflow-heading"
                title="How a session is collected"
                description="Every stage is started by a person. Nothing here chains to the next."
              />
              <CardBody>
                <ol className="space-y-4">
                  {[
                    {
                      title: "Resolve the channel",
                      body: "On this page. ClipperStash stores who the channel is, not what it is doing.",
                    },
                    {
                      title: "Check live status",
                      body: "Opens a stream session when the channel is broadcasting.",
                    },
                    {
                      title: "Collect chat",
                      body: "Run monitor_chat against that session for as long as you want to study.",
                    },
                    {
                      title: "Detect moments",
                      body: "Run detect_moments over the collected chat, or replay it with evaluate_moments.",
                    },
                    {
                      title: "Review",
                      body: "Everything recorded shows up on the dashboard.",
                    },
                  ].map((step, index) => (
                    <li key={step.title} className="flex gap-3.5">
                      <span
                        aria-hidden
                        className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border border-border bg-raised text-xs font-medium tabular-nums text-muted"
                      >
                        {index + 1}
                      </span>
                      <div className="min-w-0 space-y-0.5">
                        <p className="text-sm font-medium">{step.title}</p>
                        <p className="text-sm leading-relaxed text-muted">{step.body}</p>
                      </div>
                    </li>
                  ))}
                </ol>
              </CardBody>
            </Card>
          </section>
        </div>
      </div>
    </div>
  );
}
