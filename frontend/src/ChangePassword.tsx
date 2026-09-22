import { useState } from "react";
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
      <form className="login-card" onSubmit={form.handleSubmit}>
        <div className="login-logo" />
        <h1>{userName ? `Bienvenido, ${userName}` : "Bienvenido"}</h1>
        <p>
          Tu cuenta se creó con una contraseña temporal. Antes de continuar, escribe tu nueva
          contraseña.
        </p>
        <input
          type="password"
          placeholder="Nueva contraseña"
          value={form.newPassword}
          onChange={(event) => form.setNewPassword(event.target.value)}
          autoFocus
          required
        />
        <input
          type="password"
          placeholder="Confirma tu nueva contraseña"
          value={form.confirmPassword}
          onChange={(event) => form.setConfirmPassword(event.target.value)}
          required
        />
        <div className="password-hint">Mínimo {MIN_LENGTH} caracteres.</div>
        {form.error && <div className="login-error">{form.error}</div>}
        <button type="submit" disabled={form.isSubmitting}>
          {form.isSubmitting ? "Guardando…" : "Actualizar contraseña"}
        </button>
        <button type="button" className="login-secondary" onClick={onLogout}>
          Cerrar sesión
        </button>
      </form>
    </div>
  );
}

// Modal voluntario: cualquier usuario puede cambiar su contraseña en
// cualquier momento desde el sidebar, no solo cuando esta obligado.
export function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const [succeeded, setSucceeded] = useState(false);
  const form = useChangePasswordForm(() => setSucceeded(true));

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(event) => event.stopPropagation()}>
        <h2>Cambiar contraseña</h2>
        {succeeded ? (
          <>
            <div className="password-success">Tu contraseña se actualizó correctamente.</div>
            <button type="button" onClick={onClose}>
              Cerrar
            </button>
          </>
        ) : (
          <form onSubmit={form.handleSubmit}>
            <input
              type="password"
              placeholder="Nueva contraseña"
              value={form.newPassword}
              onChange={(event) => form.setNewPassword(event.target.value)}
              autoFocus
              required
            />
            <input
              type="password"
              placeholder="Confirma tu nueva contraseña"
              value={form.confirmPassword}
              onChange={(event) => form.setConfirmPassword(event.target.value)}
              required
            />
            <div className="password-hint">Mínimo {MIN_LENGTH} caracteres.</div>
            {form.error && <div className="login-error">{form.error}</div>}
            <div className="modal-actions">
              <button type="button" className="login-secondary" onClick={onClose}>
                Cancelar
              </button>
              <button type="submit" disabled={form.isSubmitting}>
                {form.isSubmitting ? "Guardando…" : "Guardar"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
