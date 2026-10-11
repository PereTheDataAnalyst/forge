import { useEffect, useId, useRef, type ReactNode } from "react";

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /** Prevent closing by backdrop click or Escape, for one-time secrets. */
  persistent?: boolean;
}

/** Accessible modal built on the native dialog element (focus trap and Escape come for free). */
export function Modal({ open, title, onClose, children, footer, persistent = false }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="modal"
      aria-labelledby={titleId}
      onClose={onClose}
      onCancel={(event) => {
        if (persistent) event.preventDefault();
      }}
      onClick={(event) => {
        if (!persistent && event.target === ref.current) onClose();
      }}
    >
      {open ? (
        <div className="modal__panel">
          <h2 id={titleId} className="modal__title">
            {title}
          </h2>
          <div className="modal__content">{children}</div>
          {footer ? <div className="modal__footer">{footer}</div> : null}
        </div>
      ) : null}
    </dialog>
  );
}
