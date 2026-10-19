"""Acceso a la base de conocimiento por rol.

Cada KBArticle tiene un access_level (el rol MINIMO que puede leerlo) y
cada usuario tiene un rol; un usuario puede leer los articulos cuyo
nivel es igual o menor que su rol. Se reutiliza la misma jerarquia de
roles de toda la plataforma (app.users.models.ROLE_HIERARCHY), no se
inventa una escala aparte.
"""

from __future__ import annotations

from app.rag.models import ACCESS_LEVELS
from app.users.models import ROLE_HIERARCHY, UserRole


def normalize_access_level(value: str | UserRole | None) -> str:
    """Valida un nivel recibido de la API o de un loader; None = 'viewer'
    (visible para toda la organizacion)."""
    if value is None:
        return "viewer"
    level = value.value if isinstance(value, UserRole) else str(value).lower()
    if level not in ACCESS_LEVELS:
        raise ValueError(
            f"Nivel de acceso '{value}' invalido (validos: {', '.join(ACCESS_LEVELS)})"
        )
    return level


def readable_levels(viewer_role: str | UserRole | None) -> list[str] | None:
    """Niveles de articulo que `viewer_role` puede leer. None (rol
    desconocido) significa SIN filtro: es lo que usan los procesos
    internos (evals, scripts de ingesta), nunca una peticion de un
    usuario -- los endpoints siempre pasan el rol del usuario."""
    if viewer_role is None:
        return None
    role = UserRole(viewer_role) if not isinstance(viewer_role, UserRole) else viewer_role
    rank = ROLE_HIERARCHY[role]
    return [level for level in ACCESS_LEVELS if ROLE_HIERARCHY[UserRole(level)] <= rank]
