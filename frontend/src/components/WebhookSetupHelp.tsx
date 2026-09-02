import { useState } from "react";
import { Modal } from "./Modal";
import { SlackIcon, DiscordIcon } from "../icons";

type Platform = "slack" | "discord";

const STEPS: Record<Platform, { title: string; body: string }[]> = {
  slack: [
    { title: "Create a Slack app", body: 'Go to api.slack.com/apps and click "Create New App" → "From scratch." Give it a name and pick the workspace you want notifications posted to.' },
    { title: "Turn on Incoming Webhooks", body: 'In the app\'s settings, open "Incoming Webhooks" in the left sidebar and switch the toggle on.' },
    { title: "Add the webhook to a channel", body: 'Click "Add New Webhook to Workspace," pick the channel that should receive these notifications, then click "Allow."' },
    { title: "Copy the webhook URL", body: "Slack shows a URL starting with https://hooks.slack.com/services/... — copy it and paste it into the field above." },
  ],
  discord: [
    { title: "Open your server's settings", body: 'In Discord, click your server\'s name at the top of the channel list, then "Server Settings."' },
    { title: "Go to Integrations", body: 'Select "Integrations" in the left sidebar, then "Webhooks."' },
    { title: "Create a new webhook", body: 'Click "New Webhook," give it a name, and choose the channel it should post to.' },
    { title: "Copy the webhook URL", body: 'Click "Copy Webhook URL" — it starts with https://discord.com/api/webhooks/... — and paste it into the field above.' },
  ],
};

// A simplified, schematic recreation of the screen you'll be looking at for
// the last step -- not a live screenshot (this help panel has to keep
// working even if Slack/Discord redesign their real settings pages), but
// enough of a visual match to know you're in the right place.
function SlackMockup() {
  return (
    <svg viewBox="0 0 400 190" width="100%" height="auto" role="img" aria-label="Slack Incoming Webhooks screen showing a generated webhook URL and a Copy button">
      <rect x="0.5" y="0.5" width="399" height="189" rx="10" fill="var(--surface)" stroke="var(--border)" />
      <circle cx="18" cy="18" r="4" fill="#e5534b" />
      <circle cx="32" cy="18" r="4" fill="#dbab09" />
      <circle cx="46" cy="18" r="4" fill="#28a745" />
      <text x="16" y="46" fontSize="13" fontWeight="700" fill="#4A154B">Incoming Webhooks</text>
      <text x="16" y="64" fontSize="10" fill="var(--text-secondary)">Activate Incoming Webhooks</text>
      <rect x="330" y="53" width="52" height="20" rx="10" fill="#2EB67D" />
      <circle cx="371" cy="63" r="7" fill="#fff" />
      <line x1="16" y1="82" x2="384" y2="82" stroke="var(--border)" />
      <text x="16" y="103" fontSize="11" fontWeight="600" fill="var(--text)">Webhook URLs for Your Workspace</text>
      <rect x="16" y="112" width="270" height="26" rx="5" fill="var(--bg)" stroke="var(--border)" />
      <text x="24" y="129" fontSize="9.5" fontFamily="ui-monospace, monospace" fill="var(--text-secondary)">https://hooks.slack.com/services/T0.../B0.../xxxx</text>
      <rect x="294" y="112" width="90" height="26" rx="5" fill="#4A154B" />
      <text x="339" y="129" fontSize="10.5" fontWeight="700" fill="#fff" textAnchor="middle">Copy</text>
      <text x="16" y="160" fontSize="10" fill="var(--text-tertiary)">#general · posts as "C-ECM"</text>
    </svg>
  );
}

function DiscordMockup() {
  return (
    <svg viewBox="0 0 400 190" width="100%" height="auto" role="img" aria-label="Discord Webhooks screen showing a webhook entry and a Copy Webhook URL button">
      <rect x="0.5" y="0.5" width="399" height="189" rx="10" fill="var(--surface)" stroke="var(--border)" />
      <circle cx="18" cy="18" r="4" fill="#e5534b" />
      <circle cx="32" cy="18" r="4" fill="#dbab09" />
      <circle cx="46" cy="18" r="4" fill="#28a745" />
      <text x="16" y="46" fontSize="13" fontWeight="700" fill="#5865F2">Webhooks</text>
      <rect x="330" y="34" width="54" height="22" rx="5" fill="#5865F2" />
      <text x="357" y="49" fontSize="9.5" fontWeight="700" fill="#fff" textAnchor="middle">New</text>
      <line x1="16" y1="66" x2="384" y2="66" stroke="var(--border)" />
      <rect x="16" y="80" width="368" height="70" rx="6" fill="var(--bg)" stroke="var(--border)" />
      <circle cx="42" cy="115" r="16" fill="#5865F2" />
      <text x="42" y="119" fontSize="12" fontWeight="700" fill="#fff" textAnchor="middle">C</text>
      <text x="68" y="108" fontSize="11" fontWeight="600" fill="var(--text)">C-ECM Notifications</text>
      <text x="68" y="122" fontSize="9.5" fill="var(--text-secondary)">#general</text>
      <rect x="68" y="129" width="120" height="20" rx="5" fill="#5865F2" />
      <text x="128" y="143" fontSize="9" fontWeight="700" fill="#fff" textAnchor="middle">Copy Webhook URL</text>
    </svg>
  );
}

export function WebhookSetupHelp({ initialPlatform, onClose }: { initialPlatform: Platform; onClose: () => void }) {
  const [platform, setPlatform] = useState<Platform>(initialPlatform);

  return (
    <Modal title="Connecting to Slack or Discord" onClose={onClose} width={520}>
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {(["slack", "discord"] as Platform[]).map((p) => (
          <button
            key={p}
            type="button"
            className={platform === p ? "btn-primary" : "btn-secondary"}
            style={{ fontSize: 12.5, display: "flex", alignItems: "center", gap: 6 }}
            onClick={() => setPlatform(p)}
          >
            {p === "slack" ? <SlackIcon size={16} /> : <DiscordIcon size={16} />}
            {p === "slack" ? "Slack" : "Discord"}
          </button>
        ))}
      </div>

      <ol style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 12 }}>
        {STEPS[platform].map((step, i) => (
          <li key={i} style={{ fontSize: 13 }}>
            <div style={{ fontWeight: 600 }}>{step.title}</div>
            <div className="muted" style={{ fontSize: 12.5, lineHeight: 1.5, marginTop: 2 }}>{step.body}</div>
          </li>
        ))}
      </ol>

      <div style={{ marginTop: 16 }}>
        <div className="muted" style={{ fontSize: 11, marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 700 }}>
          What that last screen looks like
        </div>
        {platform === "slack" ? <SlackMockup /> : <DiscordMockup />}
      </div>

      <p className="muted" style={{ fontSize: 11.5, marginTop: 12, marginBottom: 0 }}>
        This is a simplified recreation of that screen, not a live screenshot — {platform === "slack" ? "Slack" : "Discord"} occasionally
        redesigns their own settings pages, but the steps above stay the same.
      </p>
    </Modal>
  );
}
