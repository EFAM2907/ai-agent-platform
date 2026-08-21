# Dependencias del Proyecto — Plataforma de Agentes IA

Documento de referencia con las librerías instaladas hasta ahora, para qué sirve cada una,
y cómo se está usando concretamente en el proyecto (dominios `organizations`, `users`, `auth`).

Última actualización: correspondiente al estado del proyecto tras implementar login con JWT
y protección de rutas (`get_current_user`).

---

## 1. Framework web

### `fastapi`
Framework principal de la API. Define los routers, valida requests/responses con Pydantic,
maneja inyección de dependencias (`Depends`) y genera la documentación interactiva en `/docs`.

**Uso en el proyecto:**
- `APIRouter` en cada dominio (`organizations/api.py`, `users/api.py`, `auth/api.py`).
- `Depends()` para inyectar sesiones de BD, services, y el usuario autenticado
  (`get_db`, `get_user_service`, `get_current_user`).
- `HTTPException` para traducir excepciones internas (`UserAlreadyExistsError`,
  `InvalidCredentialsError`, etc.) a respuestas HTTP con el status code correcto.
- `Query` para parámetros de paginación (`skip`, `limit`) con validación (`le=100`).
- `OAuth2PasswordBearer` para extraer el token JWT del header `Authorization` en cada
  request protegido.

### `uvicorn`
Servidor ASGI que corre la aplicación FastAPI. Se usa con `--reload` en desarrollo para
recargar automáticamente al detectar cambios en el código.

---

## 2. Base de datos y ORM

### `sqlalchemy` (v2.0, sintaxis async)
ORM usado para modelar las tablas (`Organization`, `User`) y ejecutar queries. Se usa la
sintaxis moderna (`select()`, no `.query()`) junto con soporte async.

**Uso en el proyecto:**
- Modelos con `Column`, UUID como primary key, `relationship()` bidireccional entre
  `Organization` y `User`, `TYPE_CHECKING` para evitar imports circulares.
- `create_async_engine`, `async_sessionmaker` con `expire_on_commit=False` para el manejo
  de sesiones async.
- Patrón repository: cada repository recibe la `AsyncSession` y ejecuta las queries,
  devolviendo objetos del modelo (no dicts) en las consultas (`get_by_id`, `get_by_email`).

### `asyncpg`
Driver de PostgreSQL para SQLAlchemy en modo async. Es el que permite que `create_async_engine`
funcione con Postgres de forma no bloqueante.

### `alembic`
Herramienta de migraciones de base de datos. Se configuró con un driver síncrono aparte
(swap sync/async) porque Alembic todavía no soporta drivers async de forma nativa en su
configuración estándar.

**Uso en el proyecto:** generación y aplicación de migraciones para las tablas
`organizations` y `users`, incluyendo columnas como `deleted_at` (soft delete).

---

## 3. Validación de datos

### `pydantic` (v2)
Define los schemas de entrada/salida de cada endpoint (`UserCreate`, `UserResponse`,
`UserUpdate`, `LoginRequest`, `TokenResponse`, etc.) y valida automáticamente los datos
que llegan en cada request.

**Uso en el proyecto:**
- Separación estricta entre schema de entrada (`UserCreate`, con `password` plano) y
  schema de salida (`UserResponse`, sin `password` ni `hashed_password`) — para nunca
  exponer credenciales en las respuestas de la API.
- `model_dump()` para convertir schemas a `dict` antes de pasarlos a la capa de repository.
- `model_dump(exclude_unset=True)` específicamente en updates parciales (PATCH), para no
  sobreescribir campos que el cliente no envió.
- `EmailStr` en los campos de email (`UserCreate`, `LoginRequest`) para validación de
  formato automática — requiere el paquete `email-validator` instalado aparte.

### `email-validator`
Dependencia adicional requerida por Pydantic para que `EmailStr` funcione. Sin este paquete,
cualquier schema con `EmailStr` lanza `ModuleNotFoundError` al arrancar el servidor.

### `pydantic-settings`
Extensión de Pydantic para manejar configuración vía variables de entorno de forma tipada
y validada.

**Uso en el proyecto:**
- Clase `Settings(BaseSettings)` en `core/config.py`, que declara explícitamente cada
  variable esperada (`database_url`, `secret_key`, `algorithm`,
  `access_token_expire_minutes`, etc.) con su tipo (`str`, `int`).
- `SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")` para que lea el `.env`.
- Lección aprendida: cualquier variable en el `.env` que no esté declarada como atributo
  de la clase `Settings` rompe el arranque con `ValidationError: Extra inputs are not
  permitted` — hay que declarar cada variable nueva ahí antes de poder usarla.

---

## 4. Seguridad

### `bcrypt`
Librería de hasheo de contraseñas. Genera un salt automáticamente y produce un hash de
un solo sentido (no reversible).

**Uso en el proyecto:**
- `hash_password(plain_password)` en `core/security.py`: hashea el password antes de
  guardarlo en la base de datos (columna `hashed_password`).
- `verify_password(plain_password, hashed_password)`: compara un password en texto plano
  contra el hash guardado, usado en el flujo de login.
- Se usa en el `UserService.create()` (hashea al registrar) y en `AuthService.login()`
  (verifica al iniciar sesión).

> Nota: el roadmap original del proyecto contemplaba `argon2` en vez de `bcrypt`. Se optó
> por `bcrypt` por ser más simple y ampliamente usado; queda como decisión documentada
> (potencial ADR) más que como pendiente técnico.

### `pyjwt`
Librería para generar y decodificar JSON Web Tokens. Se importa como `jwt` en el código
aunque el paquete se instala como `pyjwt`.

**Uso en el proyecto:**
- `create_access_token(data: dict)` en `core/security.py`: arma el payload (claims `sub`,
  `organization_id`, más `exp` e `iat` agregados automáticamente) y lo firma con
  `jwt.encode()` usando `SECRET_KEY` y `ALGORITHM` (`HS256`) desde `settings`.
- `decode_access_token(token: str)`: verifica firma y expiración con `jwt.decode()`;
  lanza `InvalidTokenError` (excepción propia) si el token es inválido o expiró.
- El token generado se usa en cada request protegido vía el header
  `Authorization: Bearer <token>`.

**Configuración asociada (`.env` + `core/config.py`):**
```
SECRET_KEY=<generado con secrets.token_hex(32)>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

> Decisión de diseño: por ahora solo se implementó **access token** (sin refresh token),
> como simplificación consciente para priorizar avanzar en funcionalidad. El roadmap
> original contempla access + refresh; queda pendiente para una iteración futura.

---

## 5. Resumen de flujo: cómo se conectan estas piezas

1. **Registro** (`POST /users/`): `UserCreate` (Pydantic) → `UserService.create()` hashea
   el password con `bcrypt` → `UserRepository.create()` guarda el `User` (SQLAlchemy) en
   Postgres vía `asyncpg`.
2. **Login** (`POST /auth/login`): `LoginRequest` → `AuthService.login()` busca el usuario
   (SQLAlchemy), verifica el password con `bcrypt`, genera un JWT con `pyjwt` → devuelve
   `TokenResponse`.
3. **Acceso a rutas protegidas**: `OAuth2PasswordBearer` extrae el token del header →
   `get_current_user` (en `core/dependencies.py`) decodifica el JWT con `pyjwt`, busca el
   usuario en Postgres, y lo inyecta como `current_user` en el endpoint — con chequeos
   adicionales de rol/ownership implementados a mano en cada router.

---

## 6. Pendientes relacionados con dependencias

- Formalizar `requirements.txt` actualizado (`pip freeze > requirements.txt`) cada vez que
  se agregue una librería nueva.
- Evaluar si se migra de `bcrypt` a `argon2` para alinear con el roadmap original.
- Agregar refresh token (probablemente seguirá usando `pyjwt`, sin librería nueva).
- Cuando se retome Fase 0 (tests/CI): `pytest`, `pytest-asyncio`, `testcontainers`, `ruff`,
  `mypy` — aún no instaladas.