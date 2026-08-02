// Shared UI primitives imported by the feature pages. Deliberately minimal and
// hand-written — no component library, no CSS-in-JS.

import type {
  ButtonHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { ApiError } from "../api/client";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger";
};

export function Button({ variant = "primary", className, ...props }: ButtonProps) {
  return <button className={`btn btn-${variant} ${className ?? ""}`.trim()} {...props} />;
}

export function TextArea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`textarea ${className ?? ""}`.trim()} {...props} />;
}

type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  options: { value: string; label: string }[];
};

export function Select({ options, className, ...props }: SelectProps) {
  return (
    <select className={`select ${className ?? ""}`.trim()} {...props}>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

type Column<T> = {
  header: string;
  cell: (row: T) => ReactNode;
};

type TableProps<T> = {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  empty?: ReactNode;
};

export function Table<T>({ columns, rows, rowKey, empty }: TableProps<T>) {
  if (rows.length === 0 && empty !== undefined) {
    return <div className="table-empty">{empty}</div>;
  }
  return (
    <table className="table">
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column.header}>{column.header}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)}>
            {columns.map((column) => (
              <td key={column.header}>{column.cell(row)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type BadgeProps = {
  children: ReactNode;
  tone?: "neutral" | "success" | "warning" | "danger";
};

export function Badge({ children, tone = "neutral" }: BadgeProps) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Spinner() {
  return <span className="spinner" role="status" aria-label="Loading" />;
}

type ErrorBannerProps = {
  error: unknown;
  onDismiss?: () => void;
};

export function ErrorBanner({ error, onDismiss }: ErrorBannerProps) {
  if (!error) {
    return null;
  }
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      {onDismiss && (
        <button className="error-banner-close" onClick={onDismiss} aria-label="Dismiss">
          ×
        </button>
      )}
    </div>
  );
}

type ModalProps = {
  open: boolean;
  title?: ReactNode;
  onClose: () => void;
  children: ReactNode;
};

export function Modal({ open, title, onClose, children }: ModalProps) {
  if (!open) {
    return null;
  }
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        {title && <div className="modal-header">{title}</div>}
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  busy?: boolean;
  error?: unknown;
  onConfirm: () => void;
  onClose: () => void;
};

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Delete",
  busy = false,
  error,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  // A ReferencedError means the delete is blocked by dependent runs; surface the
  // count instead of the raw message and keep confirm disabled.
  const referenced =
    error instanceof ApiError && error.type === "ReferencedError" ? error : null;
  const runCount = referenced ? Number(referenced.detail?.run_count ?? 0) : 0;
  return (
    <Modal open={open} title={title} onClose={onClose}>
      <p className="confirm-dialog-message">{message}</p>
      {referenced ? (
        <p className="destructive-warning">
          {runCount} runs depend on this. Delete them first.
        </p>
      ) : (
        <ErrorBanner error={error} />
      )}
      <div className="form-actions">
        <Button variant="secondary" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" onClick={onConfirm} disabled={busy || referenced !== null}>
          {busy ? <Spinner /> : confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
