import { AlertCircle, Check, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, changePassword } from "./api";

const MIN_LENGTH = 8;

// Compartido por ChangePasswordScreen (obligatorio, primer login) y
// ChangePasswordModal (voluntario, desde el sidebar) -- misma
// validacion y misma llamada, solo cambia el texto alrededor y que
// pasa despues de un cambio exitoso.
function useChangePasswordForm(onChanged: () => void) {
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (newPassword.length < MIN_LENGTH) {
      setError(`La contraseña debe tener al menos ${MIN_LENGTH} caracteres`);
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("Las contraseñas no coinciden");
      return;
    }
    setIsSubmitting(true);
    setError(null);
    try {
      await changePassword(newPassword);
      setNewPassword("");
      setConfirmPassword("");
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo cambiar la contraseña");
    } finally {
      setIsSubmitting(false);
    }
  }

  return {
    newPassword,
    setNewPassword,
    confirmPassword,
    setConfirmPassword,
    error,
    isSubmitting,
    handleSubmit,
  };
}

function Fields({ form, idPrefix }: { form: ReturnType<typeof useChangePasswordForm>; idPrefix: string }) {
  return (
    <>
      <div className="field">
        <label htmlFor={`${idPrefix}-new`}>Nueva contraseña</label>
        <input
          id={`${idPrefix}-new`}
          type="password"
          autoComplete="new-password"
          value={form.newPassword}
          onChange={(event) => form.setNewPassword(event.target.value)}
          autoFocus
          required
        />
      </div>
      <div className="field">
        <label htmlFor={`${idPrefix}-confirm`}>Confirma tu nueva contraseña</label>
        <input
          id={`${idPrefix}-confirm`}
          type="password"
          autoComplete="new-password"
          value={form.confirmPassword}
          onChange={(event) => form.setConfirmPassword(event.target.value)}
          required
        />
      </div>
      <div className="password-hint">Mínimo {MIN_LENGTH} caracteres.</div>
      {form.error && (
        <div className="login-error" role="alert">
          <AlertCircle size={16} strokeWidth={1.75} />
          <span>{form.error}</span>
        </div>
      )}
    </>
  );
}

function SubmitLabel({ submitting, idle }: { submitting: boolean; idle: string }) {
  return submitting ? (
    <>
      <Loader2 size={16} strokeWidth={1.75} className="spin" />
      Guardando…
    </>
  ) : (
    <>{idle}</>
  );
}

// Pantalla completa, bloqueante -- se muestra en vez del chat mientras
// User.must_change_password siga en true (el backend ya rechaza
// /chat/messages con 403 en ese estado, ver require_password_changed;
// esto es lo que faltaba del lado de la UI para que la cuenta tuviera
// como salir de ahi).
export function ChangePasswordScreen({
  userName,
  onChanged,
  onLogout,
}: {
  userName: string | null;
  onChanged: () => void;
  onLogout: () => void;
}) {
  const form = useChangePasswordForm(onChanged);

  return (
    <div className="login-screen">
      <div className="login-column">
        <div className="login-brand">
          <span className="logo-mark" aria-hidden="true">
            A
          </span>
          AI Support
        </div>
        <form className="login-card" onSubmit={form.handleSubmit}>
          <h1>{userName ? `Bienvenido, ${userName}` : "Bienvenido"}</h1>
          <p>
            Tu cuenta se creó con una contraseña temporal. Antes de continuar, escribe tu nueva
            contraseña.
          </p>
          <Fields form={form} idPrefix="cp-screen" />
          <button type="submit" disabled={form.isSubmitting}>
            <SubmitLabel submitting={form.isSubmitting} idle="Actualizar contraseña" />
          </button>
          <button type="button" className="btn btn--ghost" onClick={onLogout}>
            Cerrar sesión
          </button>
        </form>
      </div>
    </div>
  );
}

// Modal voluntario: cualquier usuario puede cambiar su contraseña en
// cualquier momento desde el sidebar, no solo cuando esta obligado.
export function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const [succeeded, setSucceeded] = useState(false);
  const form = useChangePasswordForm(() => setSucceeded(true));

  useEffect(() => {
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="login-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cp-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h1 id="cp-modal-title">Cambiar contraseña</h1>
        {succeeded ? (
          <>
            <div className="notice notice--success" role="status">
              <Check size={16} strokeWidth={1.75} />
              Tu contraseña se actualizó correctamente.
            </div>
            <button type="button" className="btn" onClick={onClose}>
              Cerrar
            </button>
          </>
        ) : (
          <form onSubmit={form.handleSubmit}>
            <Fields form={form} idPrefix="cp-modal" />
            <div className="modal-actions">
              <button type="button" className="btn btn--ghost" onClick={onClose}>
                Cancelar
              </button>
              <button type="submit" disabled={form.isSubmitting}>
                <SubmitLabel submitting={form.isSubmitting} idle="Guardar" />
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
