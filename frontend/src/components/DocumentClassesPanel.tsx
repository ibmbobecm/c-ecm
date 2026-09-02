/**
 * DocumentClassesPanel — the "Set Metadata" modal for one file/folder:
 * assign it a document class and fill in that class's field values.
 *
 * The admin view for managing classes themselves lives in the Settings
 * page as its own tab (ClassListView, exported from this same file).
 */
import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPost, apiPut, ApiError } from "../api/client";
import type { DocumentClass, MetadataFieldDef, ResourceMetadata, ResourceMetadataHistoryEntry } from "../types";
import { Modal } from "./Modal";
import { Icon } from "../icons";
import { formatDate } from "../utils";

// ---------- helpers ----------------------------------------------------------

function FieldTypeLabel({ type }: { type: MetadataFieldDef["type"] }) {
  const labels: Record<string, string> = { text: "Text", number: "Number", date: "Date", boolean: "Yes/No", select: "Select" };
  return <span className="muted" style={{ fontSize: 11 }}>{labels[type] ?? type}</span>;
}

// Unlike formatDate (which collapses today's entries to just a time — fine
// for a single "last modified" stamp), a history list can hold several
// entries from the same day, so every row needs both the date and the time
// to stay distinguishable.
function formatDateTime(iso: string): string {
  const hasTz = /(Z|[+-]\d{2}:\d{2})$/.test(iso);
  const d = new Date(hasTz ? iso : iso + "Z");
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// ---------- Class list (admin) -----------------------------------------------

export function ClassListView() {
  const [classes, setClasses] = useState<DocumentClass[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newFields, setNewFields] = useState<MetadataFieldDef[]>([]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const reload = () => {
    setLoading(true);
    apiGet<DocumentClass[]>("/metadata/classes")
      .then(setClasses)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load classes."))
      .finally(() => setLoading(false));
  };
  useEffect(reload, []);

  const addField = () =>
    setNewFields((f) => [...f, { key: "", label: "", type: "text", required: false, options: [] }]);

  const updateField = (i: number, patch: Partial<MetadataFieldDef>) =>
    setNewFields((f) => f.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));

  const removeField = (i: number) => setNewFields((f) => f.filter((_, idx) => idx !== i));

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    // The backend schema doesn't require key/label to be non-blank or
    // unique — nothing stops submitting a field with an empty key, and
    // two fields sharing a key would silently share one slot in the
    // metadata editor's values object (keyed by field.key).
    for (const f of newFields) {
      if (!f.key.trim() || !f.label.trim()) {
        setFormError("Every field needs both a key and a label.");
        return;
      }
    }
    const keys = newFields.map((f) => f.key.trim());
    if (new Set(keys).size !== keys.length) {
      setFormError("Field keys must be unique within a class.");
      return;
    }
    setBusy(true);
    try {
      await apiPost("/metadata/classes", { name: newName, description: newDesc || null, fields: newFields });
      setShowCreate(false);
      setNewName("");
      setNewDesc("");
      setNewFields([]);
      reload();
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "Couldn't create class.");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (cls: DocumentClass) => {
    if (!window.confirm(`Delete document class "${cls.name}"? Existing metadata values will lose their class reference.`)) return;
    try {
      await apiDelete(`/metadata/classes/${cls.id}`);
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't delete class.");
    }
  };

  return (
    <div className="settings-tab-pane">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <span className="muted" style={{ fontSize: 13 }}>{classes.length} class{classes.length !== 1 ? "es" : ""}</span>
        <button className="btn-primary" style={{ fontSize: 13 }} onClick={() => setShowCreate((s) => !s)}>
          <Icon name="plus" size={13} /> New class
        </button>
      </div>

      {showCreate && (
        <form className="auth-form" onSubmit={handleCreate} style={{ background: "var(--surface)", padding: 16, borderRadius: 8, marginBottom: 16, border: "1px solid var(--border)" }}>
          <h4 style={{ margin: "0 0 12px" }}>New document class</h4>
          <label>Name <input required value={newName} onChange={(e) => setNewName(e.target.value)} /></label>
          <label>Description <input value={newDesc} onChange={(e) => setNewDesc(e.target.value)} /></label>

          <div style={{ marginBottom: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <span style={{ fontSize: 13, fontWeight: 500 }}>Fields</span>
              <button type="button" className="btn-secondary" style={{ fontSize: 12 }} onClick={addField}>+ Add field</button>
            </div>
            {newFields.map((f, i) => (
              <div key={i} style={{ marginBottom: 6 }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto auto", gap: 6, alignItems: "center" }}>
                  <input placeholder="key (no spaces)" value={f.key} onChange={(e) => updateField(i, { key: e.target.value.replace(/\s/g, "_") })} />
                  <input placeholder="Label" value={f.label} onChange={(e) => updateField(i, { label: e.target.value })} />
                  <select value={f.type} onChange={(e) => updateField(i, { type: e.target.value as MetadataFieldDef["type"] })}>
                    <option value="text">Text</option>
                    <option value="number">Number</option>
                    <option value="date">Date</option>
                    <option value="boolean">Yes/No</option>
                    <option value="select">Select</option>
                  </select>
                  <button type="button" className="icon-btn" onClick={() => removeField(i)} title="Remove field">
                    <Icon name="close" size={13} />
                  </button>
                </div>
                {f.type === "select" && (
                  <input
                    placeholder="Options, comma-separated (e.g. Draft, Final, Archived)"
                    value={f.options.join(", ")}
                    onChange={(e) =>
                      updateField(i, {
                        options: e.target.value.split(",").map((o) => o.trim()).filter(Boolean),
                      })
                    }
                    style={{ marginTop: 4, width: "100%", boxSizing: "border-box" }}
                  />
                )}
              </div>
            ))}
            {newFields.length === 0 && <p className="muted" style={{ fontSize: 12, margin: 0 }}>No fields yet — add at least one.</p>}
          </div>

          {formError && <div className="auth-error">{formError}</div>}
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" disabled={busy}>{busy ? "Creating…" : "Create"}</button>
            <button type="button" className="btn-secondary" onClick={() => setShowCreate(false)}>Cancel</button>
          </div>
        </form>
      )}

      {error && <div className="auth-error" style={{ marginBottom: 12 }}>{error}</div>}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : classes.length === 0 ? (
        <p className="muted" style={{ textAlign: "center", padding: 32 }}>No document classes yet. Create one above.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {classes.map((cls) => (
            <div key={cls.id} style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{cls.name}</div>
                  {cls.description && <div className="muted" style={{ fontSize: 12 }}>{cls.description}</div>}
                </div>
                <button className="icon-btn" title="Delete class" style={{ color: "var(--danger, #e53e3e)" }} onClick={() => handleDelete(cls)}>
                  <Icon name="trash" size={14} />
                </button>
              </div>
              {cls.fields.length > 0 && (
                <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {cls.fields.map((f) => (
                    <span key={f.key} style={{ fontSize: 11, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4, padding: "2px 7px" }}>
                      {f.label} <FieldTypeLabel type={f.type} />
                      {f.required && <span style={{ color: "#e53e3e", marginLeft: 2 }}>*</span>}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------- Metadata editor (per resource) -----------------------------------

function formatFieldValue(field: MetadataFieldDef, raw: unknown): string {
  if (raw === undefined || raw === null || raw === "") return "—";
  if (field.type === "boolean") return raw ? "Yes" : "No";
  if (field.type === "date") return formatDate(String(raw)) || "—";
  return String(raw);
}

function classLabel(classId: string | null, classes: DocumentClass[]): string {
  if (!classId) return "— None —";
  return classes.find((c) => c.id === classId)?.name ?? "Unknown class";
}

// One row per thing that actually changed in a history entry — the class
// itself (if reassigned) plus each field whose value differs, resolving
// each field's label/type against whichever class (new, falling back to
// old) still defines that key so a label survives even after a class is
// later edited or deleted.
function diffEntry(entry: ResourceMetadataHistoryEntry, classes: DocumentClass[]): { label: string; before: string; after: string }[] {
  const rows: { label: string; before: string; after: string }[] = [];
  if (entry.old_class_id !== entry.new_class_id) {
    rows.push({ label: "Document class", before: classLabel(entry.old_class_id, classes), after: classLabel(entry.new_class_id, classes) });
  }
  const newClass = classes.find((c) => c.id === entry.new_class_id);
  const oldClass = classes.find((c) => c.id === entry.old_class_id);
  const keys = new Set([...Object.keys(entry.old_values), ...Object.keys(entry.new_values)]);
  for (const key of keys) {
    const before = entry.old_values[key];
    const after = entry.new_values[key];
    if (JSON.stringify(before) === JSON.stringify(after)) continue;
    const field = newClass?.fields.find((f) => f.key === key) ?? oldClass?.fields.find((f) => f.key === key);
    rows.push({
      label: field?.label ?? key,
      before: field ? formatFieldValue(field, before) : String(before ?? "—"),
      after: field ? formatFieldValue(field, after) : String(after ?? "—"),
    });
  }
  return rows;
}

export function MetadataEditorContent({
  resourceId,
  resourceType,
}: {
  resourceId: string;
  resourceType: "file" | "folder";
}) {
  const [classes, setClasses] = useState<DocumentClass[]>([]);
  const [meta, setMeta] = useState<ResourceMetadata | null>(null);
  const [history, setHistory] = useState<ResourceMetadataHistoryEntry[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selectedClassId, setSelectedClassId] = useState<string>("");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [applyToChildren, setApplyToChildren] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  // Whether a class is already assigned decides the initial mode: nothing
  // to look at yet jumps straight to picking one, otherwise show the
  // read-only summary (matching the rest of Properties) behind a pencil.
  const [editing, setEditing] = useState(false);

  const reloadHistory = () =>
    apiGet<ResourceMetadataHistoryEntry[]>(`/metadata/resource/${resourceId}/history?resource_type=${resourceType}`).then(setHistory).catch(() => {});

  useEffect(() => {
    Promise.all([
      apiGet<DocumentClass[]>("/metadata/classes"),
      apiGet<ResourceMetadata | null>(`/metadata/resource/${resourceId}?resource_type=${resourceType}`).catch(() => null),
      apiGet<ResourceMetadataHistoryEntry[]>(`/metadata/resource/${resourceId}/history?resource_type=${resourceType}`).catch(() => []),
    ]).then(([cls, m, hist]) => {
      setClasses(cls);
      if (m) {
        setMeta(m);
        setSelectedClassId(m.class_id ?? "");
        setValues(m.values ?? {});
      }
      setHistory(hist);
      setEditing(!m?.class_id);
    }).catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load metadata."))
      .finally(() => setLoading(false));
  }, [resourceId]);

  const activeClass = classes.find((c) => c.id === selectedClassId);
  const savedClass = classes.find((c) => c.id === meta?.class_id);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const updated = await apiPut<ResourceMetadata>(`/metadata/resource/${resourceId}`, {
        resource_type: resourceType,
        class_id: selectedClassId || null,
        values,
        apply_to_children: resourceType === "folder" ? applyToChildren : undefined,
      });
      setMeta(updated);
      setSaved(
        updated.applied_to_count
          ? `Saved — also applied to ${updated.applied_to_count} item${updated.applied_to_count === 1 ? "" : "s"} inside this folder.`
          : "Saved."
      );
      setEditing(false);
      reloadHistory();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't save metadata.");
    } finally {
      setBusy(false);
    }
  };

  const setFieldValue = (key: string, val: unknown) => setValues((prev) => ({ ...prev, [key]: val }));

  const cancelEdit = () => {
    setSelectedClassId(meta?.class_id ?? "");
    setValues(meta?.values ?? {});
    setApplyToChildren(false);
    setError(null);
    setEditing(false);
  };

  if (loading) return <p className="muted">Loading…</p>;

  if (!editing) {
    return (
      <div className="viewer-metadata-view">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>Document class</span>
          <button type="button" className="icon-btn" title="Edit metadata" aria-label="Edit metadata" onClick={() => setEditing(true)}>
            <Icon name="rename" size={14} />
          </button>
        </div>
        {saved && <div className="auth-success" style={{ marginTop: 6 }}>{saved}</div>}
        {savedClass ? (
          <dl className="viewer-properties-list" style={{ marginTop: 6 }}>
            <dt>Class</dt>
            <dd>{savedClass.name}</dd>
            {savedClass.fields.map((f) => (
              <div key={f.key} style={{ display: "contents" }}>
                <dt>{f.label}</dt>
                <dd>{formatFieldValue(f, meta?.values?.[f.key])}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>No document class assigned.</p>
        )}

        {history.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <button
              type="button"
              className="link-btn"
              style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}
              onClick={() => setHistoryOpen((o) => !o)}
            >
              <Icon name={historyOpen ? "chevron-down" : "chevron-right"} size={12} />
              History ({history.length})
            </button>
            {historyOpen && (
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8 }}>
                {history.map((h) => {
                  const rows = diffEntry(h, classes);
                  return (
                    <div key={h.id} style={{ borderLeft: "2px solid var(--border)", paddingLeft: 10 }}>
                      <div className="muted" style={{ fontSize: 11 }}>
                        {formatDateTime(h.changed_at)}{h.changed_by ? ` · ${h.changed_by}` : ""}
                      </div>
                      {rows.length === 0 ? (
                        <div className="muted" style={{ fontSize: 12 }}>No field changes recorded.</div>
                      ) : (
                        rows.map((r, i) => (
                          <div key={i} style={{ fontSize: 12.5 }}>
                            <span style={{ fontWeight: 600 }}>{r.label}:</span> {r.before} → {r.after}
                          </div>
                        ))
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <form className="auth-form" onSubmit={handleSave}>
      {meta && <p className="muted" style={{ margin: "-4px 0 4px", fontSize: 12 }}>Last updated {formatDate(meta.updated_at)}</p>}
      <label>
        Document class
        <select value={selectedClassId} onChange={(e) => { setSelectedClassId(e.target.value); setValues({}); }}>
          <option value="">— None —</option>
          {classes.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </label>

      {activeClass && activeClass.fields.length > 0 && (
        <div style={{ marginTop: 4 }}>
          {activeClass.fields.map((f) => (
            <label key={f.key}>
              {f.label}
              {f.required && <span style={{ color: "#e53e3e", marginLeft: 3 }}>*</span>}
              {f.type === "boolean" ? (
                <input
                  type="checkbox"
                  checked={!!values[f.key]}
                  onChange={(e) => setFieldValue(f.key, e.target.checked)}
                  style={{ width: "auto", marginLeft: 8 }}
                />
              ) : f.type === "select" ? (
                <select value={(values[f.key] as string) ?? ""} onChange={(e) => setFieldValue(f.key, e.target.value)}>
                  <option value="">—</option>
                  {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : (
                <input
                  type={f.type === "number" ? "number" : f.type === "date" ? "date" : "text"}
                  value={(values[f.key] as string) ?? ""}
                  onChange={(e) => setFieldValue(f.key, f.type === "number" ? Number(e.target.value) : e.target.value)}
                  required={f.required}
                />
              )}
            </label>
          ))}
        </div>
      )}

      {resourceType === "folder" && (
        <label style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 6, fontWeight: 400 }}>
          <input type="checkbox" checked={applyToChildren} onChange={(e) => setApplyToChildren(e.target.checked)} style={{ width: "auto" }} />
          Apply to every file and folder inside this folder
        </label>
      )}

      {error && <div className="auth-error">{error}</div>}
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save metadata"}</button>
        {meta?.class_id && (
          <button type="button" className="btn-secondary" onClick={cancelEdit}>Cancel</button>
        )}
      </div>
    </form>
  );
}

// ---------- Public export — the per-resource "Set Metadata" modal -----------

export function DocumentClassesPanel({
  onClose,
  resourceId,
  resourceType,
  resourceName,
}: {
  onClose: () => void;
  resourceId: string;
  resourceType: "file" | "folder";
  resourceName: string;
}) {
  return (
    <Modal title={`Metadata — ${resourceName}`} onClose={onClose} width={480}>
      <MetadataEditorContent resourceId={resourceId} resourceType={resourceType} />
    </Modal>
  );
}
