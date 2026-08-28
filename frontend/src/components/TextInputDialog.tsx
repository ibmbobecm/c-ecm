import { useState } from "react";
import { Modal } from "./Modal";

export function TextInputDialog({
  title,
  initialValue = "",
  confirmLabel = "Save",
  placeholder,
  onConfirm,
  onClose,
}: {
  title: string;
  initialValue?: string;
  confirmLabel?: string;
  placeholder?: string;
  onConfirm: (value: string) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState(initialValue);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;
    onConfirm(trimmed);
    onClose();
  };

  return (
    <Modal title={title} onClose={onClose} width={380}>
      <form onSubmit={submit}>
        <input
          className="text-dialog-input"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          autoFocus
          onFocus={(e) => e.target.select()}
        />
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={!value.trim()}>
            {confirmLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}
