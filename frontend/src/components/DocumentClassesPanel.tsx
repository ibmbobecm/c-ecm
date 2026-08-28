/**
 * DocumentClassesPanel — manage document classes (schemas) and view / edit
 * metadata values on individual files.
 *
 * Two modes:
 *   1. No `resourceId` prop → shows the global list of document classes
 *      (admin view to create / delete classes).
 *   2. With `resourceId` prop → shows the metadata editor for that resource
 *      so users can assign a class and fill in field values.
 */
import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPost, apiPut, ApiError } from "../api/client";
import type { DocumentClass, MetadataFieldDef, ResourceMetadata } from "../types";
import { Modal } from "./Modal";
import { Icon } from "../icons";
import { formatDate } from "../utils";

// ---------- helpers ----------------------------------------------------------

function FieldTypeLabel({ type }: { type: MetadataFieldDef["type"] }) {
  const labels: Record<string, string> = { text: "Text", number: "Number", date: "Date", boolean: "Yes/No", select: "Select" };
  return <span className="muted" style={{ fontSize: 11 }}>{labels[type] ?? type}</span>;
}

// ---------- Class list (admin) -----------------------------------------------

function ClassListView({ onClose }: { onClose: () => void }) {
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
    <Modal title="Document Classes" onClose={onClose} width={600}>
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
    </Modal>
  );
}

// ---------- Metadata editor (per resource) -----------------------------------

function MetadataEditorView({
  resourceId,
  resourceType,
  resourceName,
  onClose,
}: {
  resourceId: string;
  resourceType: "file" | "folder";
  resourceName: string;
  onClose: () => void;
}) {
  const [classes, setClasses] = useState<DocumentClass[]>([]);
  const [meta, setMeta] = useState<ResourceMetadata | null>(null);
  const [selectedClassId, setSelectedClassId] = useState<string>("");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    Promise.all([
      apiGet<DocumentClass[]>("/metadata/classes"),
      apiGet<ResourceMetadata | null>(`/metadata/resource/${resourceId}`).catch(() => null),
    ]).then(([cls, m]) => {
      setClasses(cls);
      if (m) {
        setMeta(m);
        setSelectedClassId(m.class_id ?? "");
        setValues(m.values ?? {});
      }
    }).catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load metadata."))
      .finally(() => setLoading(false));
  }, [resourceId]);

  const activeClass = classes.find((c) => c.id === selectedClassId);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await apiPut(`/metadata/resource/${resourceId}`, {
        resource_type: resourceType,
        class_id: selectedClassId || null,
        values,
      });
      setSaved(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't save metadata.");
    } finally {
      setBusy(false);
    }
  };

  const setFieldValue = (key: string, val: unknown) => setValues((prev) => ({ ...prev, [key]: val }));

  return (
    <Modal title={`Metadata — ${resourceName}`} onClose={onClose} width={480}>
      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
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

          {error && <div className="auth-error">{error}</div>}
          {saved && <div className="auth-success">Saved.</div>}
          <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save metadata"}</button>
        </form>
      )}
    </Modal>
  );
}

// ---------- Public export — dispatcher ---------------------------------------

export function DocumentClassesPanel({
  onClose,
  resourceId,
  resourceType,
  resourceName,
}: {
  onClose: () => void;
  resourceId?: string;
  resourceType?: "file" | "folder";
  resourceName?: string;
}) {
  if (resourceId && resourceType && resourceName) {
    return (
      <MetadataEditorView
        resourceId={resourceId}
        resourceType={resourceType}
        resourceName={resourceName}
        onClose={onClose}
      />
    );
  }
  return <ClassListView onClose={onClose} />;
}
