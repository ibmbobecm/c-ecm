// One self-contained inline-SVG icon per name — no icon font, no CDN, no
// external asset. Every path uses currentColor (UI icons) or a fixed
// per-file-type fill (see fileTypeColor), so hover/active/theme changes are
// pure CSS with zero extra markup. This replaces emoji everywhere: emoji
// glyphs render as different shapes/colors across Windows/macOS/Linux and
// even across browser engines on the same OS, so two users looking at the
// same folder were literally seeing different icons.

import type { ReactElement, SVGProps } from "react";

type IconProps = {
  size?: number;
  className?: string;
};

// ---------- UI / action icons — 20x20, stroke-based ----------

function Stroke(props: SVGProps<SVGSVGElement> & { size?: number }) {
  const { size = 16, ...rest } = props;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    />
  );
}

const UI_ICONS: Record<string, (p: IconProps) => ReactElement> = {
  "chevron-down": (p) => (
    <Stroke {...p}>
      <path d="M5 7.5 10 12.5 15 7.5" />
    </Stroke>
  ),
  "chevron-right": (p) => (
    <Stroke {...p}>
      <path d="M7.5 5 12.5 10 7.5 15" />
    </Stroke>
  ),
  "more-horizontal": (p) => (
    <Stroke {...p}>
      <circle cx="4.5" cy="10" r="1.15" fill="currentColor" stroke="none" />
      <circle cx="10" cy="10" r="1.15" fill="currentColor" stroke="none" />
      <circle cx="15.5" cy="10" r="1.15" fill="currentColor" stroke="none" />
    </Stroke>
  ),
  "grid-view": (p) => (
    <Stroke {...p}>
      <rect x="3.5" y="3.5" width="5.5" height="5.5" rx="1.2" />
      <rect x="11" y="3.5" width="5.5" height="5.5" rx="1.2" />
      <rect x="3.5" y="11" width="5.5" height="5.5" rx="1.2" />
      <rect x="11" y="11" width="5.5" height="5.5" rx="1.2" />
    </Stroke>
  ),
  "list-view": (p) => (
    <Stroke {...p}>
      <path d="M4 5.5h12M4 10h12M4 14.5h12" />
    </Stroke>
  ),
  search: (p) => (
    <Stroke {...p}>
      <circle cx="8.8" cy="8.8" r="5.3" />
      <path d="m16 16-3.2-3.2" />
    </Stroke>
  ),
  upload: (p) => (
    <Stroke {...p}>
      <path d="M10 13V3.5M6 7l4-4 4 4" />
      <path d="M4 14.5V16a1.5 1.5 0 0 0 1.5 1.5h9A1.5 1.5 0 0 0 16 16v-1.5" />
    </Stroke>
  ),
  plus: (p) => (
    <Stroke {...p}>
      <path d="M10 4.5v11M4.5 10h11" />
    </Stroke>
  ),
  "folder-plus": (p) => (
    <Stroke {...p}>
      <path d="M2.5 6.2A1.7 1.7 0 0 1 4.2 4.5h3.1l1.7 1.7h6.3a1.7 1.7 0 0 1 1.7 1.7v6.4a1.7 1.7 0 0 1-1.7 1.7H4.2a1.7 1.7 0 0 1-1.7-1.7z" />
      <path d="M10 9.3v4M8 11.3h4" />
    </Stroke>
  ),
  star: (p) => (
    <Stroke {...p}>
      <path d="M10 3.2 12.2 7.9l5.1.7-3.7 3.6.9 5.1-4.5-2.4-4.5 2.4.9-5.1-3.7-3.6 5.1-.7Z" />
    </Stroke>
  ),
  trash: (p) => (
    <Stroke {...p}>
      <path d="M4 6h12M8 6V4.3a.8.8 0 0 1 .8-.8h2.4a.8.8 0 0 1 .8.8V6m-8 0 .7 9.4a1.6 1.6 0 0 0 1.6 1.5h5.4a1.6 1.6 0 0 0 1.6-1.5L15 6" />
      <path d="M8.5 9.3v4.4M11.5 9.3v4.4" />
    </Stroke>
  ),
  download: (p) => (
    <Stroke {...p}>
      <path d="M10 3v10.5M6.2 9.8l3.8 3.8 3.8-3.8" />
      <path d="M4 14.5V16a1.5 1.5 0 0 0 1.5 1.5h9A1.5 1.5 0 0 0 16 16v-1.5" />
    </Stroke>
  ),
  move: (p) => (
    <Stroke {...p}>
      <path d="M10 3.5v13M6 6l4-3.5L14 6M6 14l4 3.5 4-3.5" />
      <path d="M3.5 10h13M6 8l-2.5 2L6 12M14 8l2.5 2L14 12" strokeOpacity="0" />
    </Stroke>
  ),
  rename: (p) => (
    <Stroke {...p}>
      <path d="M12.9 3.5a1.7 1.7 0 0 1 2.4 2.4L6.4 14.8l-3.2.8.8-3.2Z" />
    </Stroke>
  ),
  close: (p) => (
    <Stroke {...p}>
      <path d="M5 5l10 10M15 5 5 15" />
    </Stroke>
  ),
  check: (p) => (
    <Stroke {...p}>
      <path d="M4.5 10.3 8 13.8l7.5-7.6" />
    </Stroke>
  ),
  "sort-asc": (p) => (
    <Stroke {...p} size={p.size ?? 12}>
      <path d="M10 15V5M6 9l4-4 4 4" />
    </Stroke>
  ),
  "sort-desc": (p) => (
    <Stroke {...p} size={p.size ?? 12}>
      <path d="M10 5v10M6 11l4 4 4-4" />
    </Stroke>
  ),
  sun: (p) => (
    <Stroke {...p}>
      <circle cx="10" cy="10" r="3.4" />
      <path d="M10 2.5v2M10 15.5v2M17.5 10h-2M4.5 10h-2M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4M15.3 15.3l-1.4-1.4M6.1 6.1 4.7 4.7" />
    </Stroke>
  ),
  moon: (p) => (
    <Stroke {...p}>
      <path d="M16.5 12.3A6.8 6.8 0 0 1 7.7 3.5 6.8 6.8 0 1 0 16.5 12.3Z" />
    </Stroke>
  ),
  "warning-triangle": (p) => (
    <Stroke {...p}>
      <path d="M10 3.3 17.8 16H2.2Z" />
      <path d="M10 8.3v3.4" />
      <circle cx="10" cy="14.1" r=".2" fill="currentColor" />
    </Stroke>
  ),
  "folder-open": (p) => (
    <Stroke {...p} size={p.size ?? 40}>
      <path d="M2.5 6.7A1.7 1.7 0 0 1 4.2 5h3.1l1.7 1.7h6.3a1.7 1.7 0 0 1 1.66 2.02l-1 5.5A1.7 1.7 0 0 1 14.3 15.7H4.65a1.7 1.7 0 0 1-1.66-1.36Z" />
    </Stroke>
  ),
  command: (p) => (
    <Stroke {...p}>
      <path d="M7 4.5A2 2 0 1 1 9 6.5V13.5A2 2 0 1 1 7 15.5V4.5Z" strokeOpacity="0" />
      <rect x="4" y="4" width="4" height="4" rx="2" />
      <rect x="12" y="4" width="4" height="4" rx="2" />
      <rect x="4" y="12" width="4" height="4" rx="2" />
      <rect x="12" y="12" width="4" height="4" rx="2" />
      <path d="M8 6h4M8 14h4M6 8v4M14 8v4" />
    </Stroke>
  ),
  link: (p) => (
    <Stroke {...p}>
      <path d="M8.3 11.7 11.7 8.3" />
      <path d="M9.5 6 11 4.5a3 3 0 0 1 4.5 4.5L14 10.5M10.5 14 9 15.5A3 3 0 0 1 4.5 11L6 9.5" />
    </Stroke>
  ),
  refresh: (p) => (
    <Stroke {...p}>
      <path d="M16 6.5A6.5 6.5 0 1 0 17 10" />
      <path d="M16 3v4h-4" />
    </Stroke>
  ),
  plug: (p) => (
    <Stroke {...p}>
      <path d="M7.5 3v4M12.5 3v4M6 7h8v3a4 4 0 0 1-8 0Z" />
      <path d="M10 14v3" />
    </Stroke>
  ),
  "bar-chart": (p) => (
    <Stroke {...p}>
      <path d="M4 16.5V11M10 16.5V4.5M16 16.5v-8" />
      <path d="M2.5 16.5h15" strokeOpacity="0.5" />
    </Stroke>
  ),
  settings: (p) => (
    <Stroke {...p} strokeLinejoin="round">
      <path d="M10 1.6 12.37 4.27 15.94 4.06 15.73 7.63 18.4 10 15.73 12.37 15.94 15.94 12.37 15.73 10 18.4 7.63 15.73 4.06 15.94 4.27 12.37 1.6 10 4.27 7.63 4.06 4.06 7.63 4.27Z" />
      <circle cx="10" cy="10" r="2.6" />
    </Stroke>
  ),
  bell: (p) => (
    <Stroke {...p}>
      <path d="M10 3.3c-2.3 0-4.1 1.9-4.1 4.1v2.4c0 .5-.2 1-.5 1.4l-1 1.2c-.5.6-.1 1.5.7 1.5h9.8c.8 0 1.2-.9.7-1.5l-1-1.2c-.3-.4-.5-.9-.5-1.4V7.4c0-2.3-1.8-4.1-4.1-4.1Z" />
      <path d="M8.3 15.8a1.7 1.7 0 0 0 3.4 0" />
    </Stroke>
  ),
  message: (p) => (
    <Stroke {...p}>
      <path d="M3.5 5.5A1.5 1.5 0 0 1 5 4h10a1.5 1.5 0 0 1 1.5 1.5v6A1.5 1.5 0 0 1 15 13H8l-3.3 2.8a.5.5 0 0 1-.82-.38V13H5a1.5 1.5 0 0 1-1.5-1.5Z" />
    </Stroke>
  ),
  signature: (p) => (
    <Stroke {...p}>
      <path d="M2.5 14.5c1.5-1 2.3-2.3 2.6-3.4.4-1.5-.2-2.6-1.1-2.4-1 .2-1 2 .3 3.4 1.6 1.7 4 1.9 5.6.2 1-1 1.4-2.8 2.4-3.8 1.3-1.3 2.6-.4 2 1.1-.5 1.3-1.9 2.9-1.1 4 .7.9 2.3.3 3.3-.8" />
      <path d="M3 17.5h14" strokeOpacity="0.5" />
    </Stroke>
  ),
  tag: (p) => (
    <Stroke {...p}>
      <path d="M3.7 4.9 9 3.5h4.8L16.5 6.2v4.8L11 16.5a1.2 1.2 0 0 1-1.7 0l-6-6a1.2 1.2 0 0 1 0-1.7Z" />
      <circle cx="12.3" cy="7.7" r="1.1" fill="currentColor" stroke="none" />
    </Stroke>
  ),
  monitor: (p) => (
    <Stroke {...p}>
      <rect x="2.5" y="4" width="15" height="10" rx="1.5" />
      <path d="M7 17.5h6M10 14v3.5" />
    </Stroke>
  ),
  logout: (p) => (
    <Stroke {...p}>
      <path d="M8.5 3.5H5a1.5 1.5 0 0 0-1.5 1.5v10A1.5 1.5 0 0 0 5 16.5h3.5" />
      <path d="M13 6.5 16.5 10 13 13.5M7.5 10h9" />
    </Stroke>
  ),
  lock: (p) => (
    <Stroke {...p}>
      <rect x="4.5" y="9" width="11" height="8.5" rx="1.5" />
      <path d="M7.5 9V6.8a2.5 2.5 0 0 1 5 0V9" />
    </Stroke>
  ),
  unlock: (p) => (
    <Stroke {...p}>
      <rect x="4.5" y="9" width="11" height="8.5" rx="1.5" />
      <path d="M7.5 9V6.8a2.5 2.5 0 0 1 5 0v0" strokeDasharray="3 2" />
    </Stroke>
  ),
  info: (p) => (
    <Stroke {...p}>
      <circle cx="10" cy="10" r="7.2" />
      <path d="M10 9.2v4.3" />
      <circle cx="10" cy="6.7" r="0.9" fill="currentColor" stroke="none" />
    </Stroke>
  ),
  eye: (p) => (
    <Stroke {...p}>
      <path d="M2.5 10S5.5 4.5 10 4.5 17.5 10 17.5 10 14.5 15.5 10 15.5 2.5 10 2.5 10Z" />
      <circle cx="10" cy="10" r="2.2" />
    </Stroke>
  ),
  "eye-off": (p) => (
    <Stroke {...p}>
      <path d="M14.4 14.4A7.5 7.5 0 0 1 10 15.5C5.5 15.5 2.5 10 2.5 10a13.3 13.3 0 0 1 3.1-3.9M8.5 5.1A8.5 8.5 0 0 1 10 4.5c4.5 0 7.5 5.5 7.5 5.5a13.3 13.3 0 0 1-1.5 2.2" />
      <path d="M11.7 11.7a2.2 2.2 0 0 1-3.1-3.1" />
      <path d="M3 3l14 14" />
    </Stroke>
  ),
  "check-circle": (p) => (
    <Stroke {...p}>
      <circle cx="10" cy="10" r="7.5" />
      <path d="M7 10.3l2.3 2.3L13.5 8" />
    </Stroke>
  ),
  workflow: (p) => (
    <Stroke {...p}>
      <rect x="3" y="4" width="5" height="4" rx="1" />
      <rect x="12" y="4" width="5" height="4" rx="1" />
      <rect x="7.5" y="13" width="5" height="4" rx="1" />
      <path d="M5.5 8v2.5a2 2 0 0 0 2 2h5a2 2 0 0 0 2-2V8" />
      <path d="M10 10.5V13" />
    </Stroke>
  ),
};

// ---------- File-type icons — 24x24, flat filled folded-corner shape ----------

const FILE_PATH = "M6 2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z";
const FOLD_PATH = "M14 2v6h6";

function FileGlyph({ size = 20, color, inset }: { size?: number; color: string; inset?: ReactElement }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path d={FILE_PATH} fill={color} />
      <path d={FOLD_PATH} fill="none" stroke="white" strokeOpacity={0.55} strokeWidth={1.2} strokeLinejoin="round" />
      {inset}
    </svg>
  );
}

function badge(text: string) {
  return (
    <text x="12" y="18.5" textAnchor="middle" fontSize="6.4" fontWeight={700} fill="white" fillOpacity={0.95} fontFamily="Inter, sans-serif">
      {text}
    </text>
  );
}

export const FILE_TYPE_COLOR: Record<string, string> = {
  doc: "#3B72E8",
  sheet: "#1E8E3E",
  slide: "#E8710A",
  pdf: "#D93025",
  image: "#9334E6",
  video: "#D6409F",
  audio: "#00838F",
  archive: "#5F6368",
  code: "#455A64",
  generic: "#80868B",
};

const FILE_ICONS: Record<string, (p: IconProps) => ReactElement> = {
  "file-doc": (p) => <FileGlyph size={p.size} color={FILE_TYPE_COLOR.doc} inset={badge("DOC")} />,
  "file-sheet": (p) => <FileGlyph size={p.size} color={FILE_TYPE_COLOR.sheet} inset={badge("XLS")} />,
  "file-slide": (p) => <FileGlyph size={p.size} color={FILE_TYPE_COLOR.slide} inset={badge("PPT")} />,
  "file-pdf": (p) => <FileGlyph size={p.size} color={FILE_TYPE_COLOR.pdf} inset={badge("PDF")} />,
  "file-image": (p) => (
    <FileGlyph
      size={p.size}
      color={FILE_TYPE_COLOR.image}
      inset={
        <g transform="translate(7.5,13)" stroke="white" strokeOpacity={0.85} strokeWidth={1.3} fill="none" strokeLinejoin="round">
          <rect x="0" y="0" width="9" height="6.5" rx="1" />
          <circle cx="2.3" cy="2.2" r="0.9" fill="white" fillOpacity={0.85} stroke="none" />
          <path d="M0.5 6 3.2 3.4 5 5.1 6.8 3 8.5 5" />
        </g>
      }
    />
  ),
  "file-video": (p) => (
    <FileGlyph
      size={p.size}
      color={FILE_TYPE_COLOR.video}
      inset={<path d="M9.5 12.3v5.2l4.6-2.6z" fill="white" fillOpacity={0.9} />}
    />
  ),
  "file-audio": (p) => (
    <FileGlyph
      size={p.size}
      color={FILE_TYPE_COLOR.audio}
      inset={
        <g stroke="white" strokeOpacity={0.85} strokeWidth={1.3} fill="none" strokeLinecap="round">
          <path d="M9.5 17V12l5-1v5" />
          <circle cx="9" cy="17" r="1.3" fill="white" fillOpacity={0.85} stroke="none" />
          <circle cx="14" cy="16" r="1.3" fill="white" fillOpacity={0.85} stroke="none" />
        </g>
      }
    />
  ),
  "file-archive": (p) => (
    <FileGlyph
      size={p.size}
      color={FILE_TYPE_COLOR.archive}
      inset={
        <g stroke="white" strokeOpacity={0.85} strokeWidth={1.3}>
          <path d="M12 11v2M12 14.4v1.4M12 17.2v1.4" />
        </g>
      }
    />
  ),
  "file-code": (p) => (
    <FileGlyph
      size={p.size}
      color={FILE_TYPE_COLOR.code}
      inset={
        <g stroke="white" strokeOpacity={0.85} strokeWidth={1.3} fill="none" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9.5 12.5 7.5 15l2 2.5M14.5 12.5l2 2.5-2 2.5" />
        </g>
      }
    />
  ),
  "file-generic": (p) => <FileGlyph size={p.size} color={FILE_TYPE_COLOR.generic} />,
  folder: (p) => {
    const size = p.size ?? 20;
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M3 6.4A2 2 0 0 1 5 4.4h4.2l2 2h7.8A2 2 0 0 1 21 8.4v9.2A2 2 0 0 1 19 19.6H5a2 2 0 0 1-2-2z"
          fill="var(--folder-color, #5B8DEF)"
        />
      </svg>
    );
  },
};

const ALL_ICONS = { ...UI_ICONS, ...FILE_ICONS };

export type IconName = keyof typeof ALL_ICONS;

export function Icon({ name, size = 16, className }: { name: IconName; size?: number; className?: string }) {
  const Cmp = ALL_ICONS[name];
  if (!Cmp) return null;
  return (
    <span className={className} style={{ display: "inline-flex", flexShrink: 0 }}>
      {Cmp({ size })}
    </span>
  );
}

export function fileTypeIconName(contentType: string | null, name: string): IconName {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (contentType?.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "svg", "webp", "bmp"].includes(ext)) return "file-image";
  if (contentType?.startsWith("video/") || ["mp4", "mov", "avi", "mkv", "webm"].includes(ext)) return "file-video";
  if (contentType?.startsWith("audio/") || ["mp3", "wav", "flac", "aac", "ogg"].includes(ext)) return "file-audio";
  if (contentType === "application/pdf" || ext === "pdf") return "file-pdf";
  if (["zip", "rar", "7z", "tar", "gz"].includes(ext)) return "file-archive";
  if (["doc", "docx", "odt", "rtf"].includes(ext)) return "file-doc";
  if (["xls", "xlsx", "csv", "ods"].includes(ext)) return "file-sheet";
  if (["ppt", "pptx", "odp"].includes(ext)) return "file-slide";
  if (["js", "ts", "tsx", "jsx", "py", "java", "c", "cpp", "cs", "go", "rs", "rb", "php", "html", "css", "json", "xml", "sh", "yml", "yaml"].includes(ext))
    return "file-code";
  return "file-generic";
}

const PROVIDER_COLOR: Record<string, string> = {
  filenet: "#3B72E8",
  local: "#5F6368",
  alfresco: "#5B913B",
  google_drive: "#1A73E8",
  onedrive_sharepoint: "#0364B8",
  box: "#0061D5",
  aws_s3: "#E8710A",
  ibm_cos: "#054ADA",
  azure_blob: "#00A2E8",
  ibm_i: "#0f62fe",
  ibm_z: "#198038",
};

const PROVIDER_LABEL: Record<string, string> = {
  filenet: "FN",
  local: "L",
  alfresco: "AF",
  google_drive: "GD",
  onedrive_sharepoint: "MS",
  box: "BX",
  aws_s3: "S3",
  ibm_cos: "COS",
  azure_blob: "AZ",
  ibm_i: "i",
  ibm_z: "Z",
};

export function ProviderBadge({ providerKey, size = 20 }: { providerKey: string; size?: number }) {
  const color = PROVIDER_COLOR[providerKey] ?? "#80868B";
  const label = PROVIDER_LABEL[providerKey] ?? "?";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: size,
        height: size,
        borderRadius: "50%",
        background: color,
        color: "white",
        fontSize: size * 0.34,
        fontWeight: 700,
        flexShrink: 0,
        letterSpacing: "-0.02em",
      }}
      aria-hidden="true"
    >
      {label}
    </span>
  );
}
