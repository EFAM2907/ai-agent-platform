from pydantic import BaseModel, EmailStr, Field

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    
class TokenResponse(BaseModel):
    access_token: str 
    token_type: str = "bearer"
    
class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # True para cuentas creadas via invitacion (contraseña temporal) que
    # todavia no cambiaron su contraseña -- el frontend debe mostrar la
    # pantalla de cambio de contraseña antes que cualquier otra cosa, y
    # el backend lo refuerza en las rutas de negocio (ver
    # app.core.dependencies.require_password_changed).
    must_change_password: bool = False

class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordChangeRequest(BaseModel):
    # Sin ningun minimo, cualquier contraseña de 1 caracter pasaba --
    # notable porque esta es la ruta que reemplaza justamente una
    # contraseña temporal generada (ver generate_temporary_password).
    new_password: str = Field(min_length=8)