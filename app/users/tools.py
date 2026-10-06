"""Tools de gestion de usuarios para el agente de chat (ver
app.llm.tools.run_tool_loop).

Cada handler reaplica EXACTAMENTE las mismas reglas de autorizacion que
app.users.api (jerarquia de roles via can_manage_other_user, y
aislamiento multi-tenant via verify_same_organization) para el `actor`
concreto que abrio la conversacion -- llamar una de estas tools desde
el chat nunca puede hacer nada que ese mismo usuario no pudiera hacer
llamando a la API REST directamente. No hay un bypass de admin: el
"admin" aca siempre es el usuario autenticado del chat, nunca un
service account con privilegios propios.

update_user deliberadamente NO expone `password` como parametro,
aunque UserUpdate lo permite -- resetear la contrasena de una cuenta
(la propia o la de otro usuario) es una accion de alto riesgo que un
agente conversacional no deberia poder disparar a partir de una
instruccion en lenguaje natural (incluida una inyectada en el mensaje
de un usuario). Ese flujo sigue existiendo, pero solo via la API REST
donde una llamada explicita y deliberada lo respalda.

create_user, si la organizacion tiene Gmail conectado, envia la
contraseña temporal por correo al nuevo usuario en vez de devolverla en
el resultado de la tool -- asi no queda expuesta en el historial de la
conversacion (visible para cualquiera con acceso al chat, no solo para
quien pidio crear la cuenta). Si Gmail no esta conectado, o si el envio
falla por lo que sea, cae al comportamiento anterior: mostrarla una
sola vez en el resultado. Nunca bloquea la creacion del usuario por un
fallo de envio -- gmail_service es una mejora, no un requisito.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.gmail.errors import GmailError
from app.gmail.outbound import render_user_invite_email
from app.gmail.service import GmailConnectionService
from app.llm.schemas import ToolDefinition
from app.llm.tools import Tool
from app.users.exceptions import UserAlreadyExistsError
from app.users.models import ROLE_HIERARCHY, User, UserRole
from app.users.schemas import UserCreate, UserUpdate
from app.users.service import UserService

logger = logging.getLogger(__name__)


def _verify_same_organization(actor: User, target_organization_id: uuid.UUID) -> None:
    """Mismo criterio que app.core.dependencies.verify_same_organization:
    is_platform_admin puede leer/operar cross-tenant, cualquier otro
    actor no -- pero, a diferencia de esa version, esta no depende de
    FastAPI (estas tools corren fuera de un request HTTP), asi que
    levanta un error Python plano en vez de HTTPException."""
    if actor.is_platform_admin:
        return
    if actor.organization_id != target_organization_id:
        raise PermissionError("User not found")


def _can_manage_other_user(actor: User, target: User) -> bool:
    return ROLE_HIERARCHY[actor.role] > ROLE_HIERARCHY[target.role]


def _require_admin(actor: User) -> None:
    if actor.role not in (UserRole.ADMIN, UserRole.OWNER):
        raise PermissionError("Admin privileges required")


def _parse_user_id(user_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(user_id)
    except (ValueError, AttributeError, TypeError):
        return None


def _serialize(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "organization_id": str(user.organization_id),
        "created_at": user.created_at.isoformat(),
    }


def build_user_management_tools(
    actor: User,
    service: UserService,
    gmail_service: GmailConnectionService | None = None,
) -> list[Tool]:
    """Arma las tools de gestion de usuarios cerradas sobre `actor` --
    el usuario autenticado que abrio esta conversacion de chat. Las
    seis se ofrecen siempre al LLM, sin filtrar por rol de antemano:
    cada handler hace su propio chequeo de permisos y devuelve un error
    claro si `actor` no puede hacer esa operacion, exactamente como le
    respondería la API REST con un 403 -- no hay necesidad de duplicar
    esa logica de "que tool mostrarle a quien" por fuera.

    gmail_service es opcional (None en cualquier caller que no tenga
    Gmail a mano, ej. tests) -- create_user simplemente cae a mostrar
    la contraseña si no se lo pasan, igual que si el envio fallara."""

    async def create_user(email: str, full_name: str, role: str = "member") -> Any:
        _require_admin(actor)
        try:
            new_role = UserRole(role.lower())
        except ValueError:
            return {"error": f"Rol '{role}' inválido. Usa: admin, member o viewer"}
        if new_role == UserRole.OWNER:
            return {"error": "No se puede crear un usuario con rol owner por este medio"}
        if actor.role == UserRole.ADMIN and new_role == UserRole.ADMIN:
            raise PermissionError("Admins can only create users below ADMIN")
        try:
            new_user, temporary_password = await service.create(
                actor, UserCreate(email=email, full_name=full_name, role=new_role)
            )
        except UserAlreadyExistsError:
            return {"error": f"Ya existe un usuario con el email '{email}'"}

        result = _serialize(new_user)
        if gmail_service is not None:
            subject, body = render_user_invite_email(
                full_name=new_user.full_name,
                email=new_user.email,
                temporary_password=temporary_password,
            )
            try:
                await gmail_service.send_email(
                    actor.organization_id, to_email=new_user.email, subject=subject, body=body
                )
            except GmailError:
                logger.warning(
                    "No se pudo enviar la invitacion por correo al usuario %s; "
                    "se muestra la contraseña en el resultado en su lugar",
                    new_user.id,
                    exc_info=True,
                )
            else:
                result["note"] = (
                    f"La contraseña temporal se envió por correo a {new_user.email}. "
                    "No se muestra aquí por seguridad; el usuario debe revisar su "
                    "bandeja de entrada y cambiarla en su primer login."
                )
                return result

        # Sin Gmail conectado, o si el envio fallo: se muestra la
        # contraseña una unica vez, como antes.
        result["temporary_password"] = temporary_password
        result["note"] = (
            "Esta es la ÚNICA vez que se muestra esta contraseña -- "
            "compártela con el usuario ahora. Deberá cambiarla en su "
            "primer login."
        )
        return result

    async def list_users(limit: int = 50) -> Any:
        _require_admin(actor)
        limit = max(1, min(limit, 100))
        users = await service.list(actor.organization_id, 0, limit)
        return [_serialize(u) for u in users]

    async def get_user(user_id: str) -> Any:
        target_id = _parse_user_id(user_id)
        if target_id is None:
            return {"error": f"'{user_id}' no es un UUID de usuario válido"}
        target = await service.get_by_id(target_id)
        if target is None or target.deleted_at is not None:
            return {"error": "Usuario no encontrado"}
        _verify_same_organization(actor, target.organization_id)
        if (
            actor.id != target.id
            and actor.role not in (UserRole.ADMIN, UserRole.OWNER)
            and not actor.is_platform_admin
        ):
            raise PermissionError("Not authorized to view this user")
        return _serialize(target)

    async def update_user(
        user_id: str, full_name: str | None = None, email: str | None = None
    ) -> Any:
        target_id = _parse_user_id(user_id)
        if target_id is None:
            return {"error": f"'{user_id}' no es un UUID de usuario válido"}
        target = await service.get_by_id(target_id)
        if target is None or target.deleted_at is not None:
            return {"error": "Usuario no encontrado"}
        _verify_same_organization(actor, target.organization_id)
        if actor.id != target.id and not _can_manage_other_user(actor, target):
            raise PermissionError("Not authorized to update this user")
        fields = {k: v for k, v in {"full_name": full_name, "email": email}.items() if v is not None}
        if not fields:
            return {"error": "Nada para actualizar: pasa full_name y/o email"}
        updated = await service.update(target_id, UserUpdate(**fields))
        return _serialize(updated)

    async def delete_user(user_id: str) -> Any:
        target_id = _parse_user_id(user_id)
        if target_id is None:
            return {"error": f"'{user_id}' no es un UUID de usuario válido"}
        target = await service.get_by_id(target_id)
        if target is None or target.deleted_at is not None:
            return {"error": "Usuario no encontrado"}
        _verify_same_organization(actor, target.organization_id)
        if actor.id == target.id and actor.role == UserRole.OWNER:
            raise PermissionError("Transfer ownership before deleting the owner account")
        if actor.id != target.id and not _can_manage_other_user(actor, target):
            raise PermissionError("Not authorized to delete this user")
        await service.delete(target_id)
        return {"deleted": True, "user_id": str(target_id)}

    async def change_user_role(user_id: str, role: str) -> Any:
        _require_admin(actor)
        target_id = _parse_user_id(user_id)
        if target_id is None:
            return {"error": f"'{user_id}' no es un UUID de usuario válido"}
        if actor.id == target_id:
            raise PermissionError("You cannot change your own role")
        target = await service.get_by_id(target_id)
        if target is None or target.deleted_at is not None:
            return {"error": "Usuario no encontrado"}
        _verify_same_organization(actor, target.organization_id)
        try:
            new_role = UserRole(role.lower())
        except ValueError:
            return {"error": f"Rol '{role}' inválido. Usa: admin, member o viewer"}
        if target.role == UserRole.OWNER or new_role == UserRole.OWNER:
            raise PermissionError("Use the ownership transfer endpoint for OWNER")
        if not _can_manage_other_user(actor, target):
            raise PermissionError("Not authorized to change this user's role")
        if actor.role == UserRole.ADMIN and new_role == UserRole.ADMIN:
            raise PermissionError("Admins can only assign roles below ADMIN")
        updated = await service.update_role(target_id, new_role)
        return _serialize(updated)

    return [
        Tool(
            definition=ToolDefinition(
                name="create_user",
                description=(
                    "Invite a new user to this organization with a generated "
                    "temporary password -- there is no shared/manual password, "
                    "every account gets its own. Requires ADMIN or OWNER role. "
                    "Admins can only assign roles below ADMIN; OWNER cannot be "
                    "assigned here. If this organization has Gmail connected, "
                    "the temporary password is emailed directly to the new "
                    "user and is NOT included in this result -- just tell the "
                    "requester it was sent by email, do not ask them to relay "
                    "it. Otherwise (no Gmail connected, or the email failed to "
                    "send) it's returned ONCE in the result instead -- relay "
                    "it to the requester immediately, it cannot be recovered "
                    "afterwards. Either way the new user must change it on "
                    "first login."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "email": {"type": "string", "description": "New user's email address"},
                        "full_name": {"type": "string", "description": "New user's full name"},
                        "role": {
                            "type": "string",
                            "enum": ["admin", "member", "viewer"],
                            "description": "Role to assign (default: member)",
                        },
                    },
                    "required": ["email", "full_name"],
                },
            ),
            handler=create_user,
        ),
        Tool(
            definition=ToolDefinition(
                name="list_users",
                description=(
                    "List active users in the current admin's organization. "
                    "Requires ADMIN or OWNER role."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max users to return (1-100, default 50)",
                        }
                    },
                    "required": [],
                },
            ),
            handler=list_users,
        ),
        Tool(
            definition=ToolDefinition(
                name="get_user",
                description=(
                    "Get a single user's details by id. Any user can look up "
                    "their own account; looking up someone else's requires "
                    "ADMIN or OWNER role."
                ),
                parameters={
                    "type": "object",
                    "properties": {"user_id": {"type": "string", "description": "UUID of the user"}},
                    "required": ["user_id"],
                },
            ),
            handler=get_user,
        ),
        Tool(
            definition=ToolDefinition(
                name="update_user",
                description=(
                    "Update a user's full_name and/or email. Any user can "
                    "update their own account; updating someone else's "
                    "requires outranking them in the role hierarchy "
                    "(owner > admin > member > viewer). Cannot change "
                    "passwords -- that requires the dedicated account flow."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "UUID of the user to update"},
                        "full_name": {"type": "string", "description": "New full name"},
                        "email": {"type": "string", "description": "New email address"},
                    },
                    "required": ["user_id"],
                },
            ),
            handler=update_user,
        ),
        Tool(
            definition=ToolDefinition(
                name="delete_user",
                description=(
                    "Soft-delete a user account. Requires outranking the "
                    "target in the role hierarchy (an OWNER cannot delete "
                    "their own account this way -- ownership must be "
                    "transferred first). Irreversible from chat: confirm "
                    "the exact person before calling this."
                ),
                parameters={
                    "type": "object",
                    "properties": {"user_id": {"type": "string", "description": "UUID of the user to delete"}},
                    "required": ["user_id"],
                },
            ),
            handler=delete_user,
        ),
        Tool(
            definition=ToolDefinition(
                name="change_user_role",
                description=(
                    "Change a user's role to admin, member or viewer. "
                    "Requires ADMIN or OWNER role, and outranking the "
                    "target. Admins cannot promote anyone to admin, and "
                    "OWNER cannot be assigned or changed here -- that "
                    "requires the ownership transfer flow."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "UUID of the user"},
                        "role": {
                            "type": "string",
                            "enum": ["admin", "member", "viewer"],
                            "description": "New role to assign",
                        },
                    },
                    "required": ["user_id", "role"],
                },
            ),
            handler=change_user_role,
        ),
    ]
