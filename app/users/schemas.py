from datetime import datetime 
from pydantic import BaseModel
from app.users.models import UserRole
import uuid

class UserCreate(BaseModel):
    """Sin `password`: el que invita (admin/owner, o la tool create_user
    del chat) nunca elige la contraseña -- UserService.create() siempre
    genera una temporal (ver app.core.security.generate_temporary_password)
    y la cuenta queda con must_change_password=True. `role` es MEMBER
    por defecto; asignar ADMIN u OWNER aca lo valida el caller (API o
    tool), no este schema -- mismas reglas que ya aplica change_role."""

    email: str
    full_name: str
    role: UserRole = UserRole.MEMBER

class UserUpdate(BaseModel):
    email: str | None = None
    full_name: str | None = None
    password: str | None = None

class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    organization_id: uuid.UUID
    created_at: datetime
    # El frontend lo usa para mostrar la pantalla de cambio de
    # contraseña obligatorio apenas carga el perfil (GET /users/me) --
    # sin esto, solo se sabia justo despues de POST /auth/login, y un
    # simple refresh de pagina con el flag todavia en True perdia esa
    # informacion (ver app.core.dependencies.require_password_changed,
    # que sigue siendo el enforcement real del lado del servidor).
    must_change_password: bool

    model_config = {"from_attributes": True}


class UserInviteResponse(UserResponse):
    """Misma forma que UserResponse mas la contraseña temporal en texto
    plano -- se devuelve UNA sola vez, en la respuesta de este mismo
    request de creacion. No se puede recuperar despues (no se persiste
    en claro); si se pierde, la unica salida es que el usuario cambie
    su contraseña o un admin lo vuelva a invitar."""

    temporary_password: str



class UserRoleUpdate(BaseModel):
    role: UserRole
    
    model_config = {"from_attributes": True}


class OwnershipTransfer(BaseModel):
    target_user_id: uuid.UUID
