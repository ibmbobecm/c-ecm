import { Icon, ProviderBadge } from "../icons";

const BACKENDS: { key: string; label: string }[] = [
  { key: "filenet", label: "IBM FileNet" },
  { key: "ibm_cos", label: "IBM Cloud Object Storage" },
  { key: "ibm_i", label: "IBM i (AS/400)" },
  { key: "ibm_z", label: "IBM Z Mainframe" },
  { key: "alfresco", label: "Alfresco" },
  { key: "google_drive", label: "Google Drive" },
  { key: "onedrive_sharepoint", label: "Microsoft 365" },
  { key: "box", label: "Box" },
  { key: "aws_s3", label: "AWS S3" },
  { key: "azure_blob", label: "Azure Blob" },
  { key: "local", label: "Local Disk" },
];

const FEATURES: { icon: Parameters<typeof Icon>[0]["name"]; title: string; body: string }[] = [
  {
    icon: "plug",
    title: "Unify Every Repository",
    body: "FileNet, mainframe datasets, AS/400 IFS, Alfresco, S3, Azure, Google Drive, OneDrive, Box — one interface over content that never has to move.",
  },
  {
    icon: "search",
    title: "Global Search",
    body: "Query every connected backend at once, in parallel, with per-connection error isolation — find a document without knowing which system holds it.",
  },
  {
    icon: "star",
    title: "AI-Powered Insights",
    body: "Watsonx-backed summaries, document Q&A, and workflow suggestions — turn a wall of PDFs into an answer in seconds.",
  },
  {
    icon: "check-circle",
    title: "Approval Workflows",
    body: "Multi-step, multi-approver review chains with quorum rules, full history, and inline approve/reject from wherever a document lives.",
  },
  {
    icon: "signature",
    title: "E-Signature",
    body: "Route documents for signature through DocuSign without leaving the platform, and track status alongside every other document event.",
  },
  {
    icon: "bar-chart",
    title: "Audit Trail & Compliance Reporting",
    body: "Every login, view, edit, and approval — logged, searchable, charted, and exportable, with automatic alerts on suspicious activity bursts.",
  },
  {
    icon: "lock",
    title: "Retention & Legal Hold",
    body: "Policy-driven retention schedules and legal holds that follow a document across its lifecycle, not just inside one silo.",
  },
  {
    icon: "message",
    title: "Real-Time Collaboration",
    body: "Tags, comments, check-out/check-in locking, and share links — the coordination layer every one of those repositories was missing on its own.",
  },
];

export function Landing({ onSignIn }: { onSignIn: () => void }) {
  return (
    <div className="landing-page">
      <header className="landing-nav">
        <div className="landing-nav-brand">
          <Icon name="folder" size={22} />
          C-ECM
        </div>
        <button className="btn-primary" onClick={onSignIn}>Sign In</button>
      </header>

      <section className="landing-hero">
        <span className="landing-eyebrow">Centralized Enterprise Content Management</span>
        <h1>
          Every repository your enterprise already runs.
          <br />
          One governed, intelligent front door.
        </h1>
        <p className="landing-hero-sub">
          C-ECM doesn't ask you to migrate. It sits across the systems you already have —
          FileNet, mainframe, cloud drives, object storage — and gives every one of them
          search, workflow, audit, and AI in a single place.
        </p>
        <div className="landing-hero-actions">
          <button className="btn-primary landing-cta" onClick={onSignIn}>
            Sign In
            <Icon name="chevron-right" size={16} />
          </button>
        </div>
      </section>

      <section className="landing-backends">
        <div className="landing-backends-label">Connects to the systems you already run</div>
        <div className="landing-backends-row">
          {BACKENDS.map((b) => (
            <div key={b.key} className="landing-backend-chip" title={b.label}>
              <ProviderBadge providerKey={b.key} size={22} />
              <span>{b.label}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-features">
        <h2>One platform, every capability an ECM needs</h2>
        <div className="landing-feature-grid">
          {FEATURES.map((f) => (
            <div key={f.title} className="landing-feature-card">
              <div className="landing-feature-icon">
                <Icon name={f.icon} size={20} />
              </div>
              <h3>{f.title}</h3>
              <p>{f.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-cta-band">
        <h2>Bring order to content scattered across a dozen systems.</h2>
        <button className="btn-primary landing-cta" onClick={onSignIn}>
          Sign In
          <Icon name="chevron-right" size={16} />
        </button>
      </section>

      <footer className="landing-footer">
        <span>C-ECM — Centralized Enterprise Content Management</span>
      </footer>
    </div>
  );
}
