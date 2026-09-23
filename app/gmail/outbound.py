"""Correos salientes sueltos desde el buzon conectado de una
organizacion -- distintos del pipeline de respuestas de tracking
(app.gmail.reply): no son una respuesta dentro de un hilo existente,
son un correo nuevo que la propia app decide enviar.

Hoy, el unico caso es la invitacion de create_user con la contraseña
temporal (ver app.users.tools.create_user y
GmailConnectionService.send_email). A diferencia de reply.py, el texto
es fijo y sin LLM: es una credencial, no algo que valga la pena redactar
con margen de interpretacion (ni exponer a un prompt).
"""

from __future__ import annotations

import base64
import re
from email.message import EmailMessage

_MAX_SUBJECT_CHARS = 200


def _clean_header(value: str) -> str:
    # Mismo criterio que app.gmail.parser._clean_header: sin saltos de
    # linea ni NUL, para no permitir inyeccion de cabeceras.
    return re.sub(r"[\r\n\x00]+", " ", value).strip()[:_MAX_SUBJECT_CHARS]


def build_plain_email_raw(*, to_email: str, from_email: str, subject: str, body: str) -> str:
    """Arma un correo nuevo (sin In-Reply-To/References: no responde a
    nada), listo para la Gmail API (base64url)."""
    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = _clean_header(subject)
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def render_user_invite_email(
    *, full_name: str, email: str, temporary_password: str
) -> tuple[str, str]:
    """(subject, body) de la invitacion a una cuenta nueva."""
    greeting = f"Hola {full_name}," if full_name else "Hola,"
    subject = "Tu cuenta ha sido creada"
    body = (
        f"{greeting}\n\n"
        f"Se creó una cuenta para ti con el correo {email}.\n\n"
        f"Tu contraseña temporal es: {temporary_password}\n\n"
        "Deberás cambiarla la primera vez que inicies sesión.\n\n"
        "Si no esperabas este correo, contacta a quien administra tu organización.\n\n"
        "Saludos cordiales."
    )
    return subject, body
