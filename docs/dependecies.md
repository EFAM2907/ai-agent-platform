# Dependencias del Proyecto — Plataforma de Agentes IA

Documento de referencia con las librerías instaladas, para qué sirve cada una, y cómo se están usando concretamente en el proyecto (dominios `organizations`, `users`, `auth`).

Última actualización: correspondiente al estado actual del proyecto tras implementar control de acceso por jerarquía de roles, propietario de organización (`OWNER`), administradores de plataforma (`is_platform_admin`), registro de organización en transacción atómica (`Bootstrap`) y transferencia segura de propiedad (`Ownership Transfer`) con bloqueo de filas.

---

## 1. Framework web

### `fastapi`
Framework principal de la API. Define los routers, valida requests/responses con Pydantic, maneja inyección de dependencias (`Depends`) y genera la documentación interactiva en `/docs`.

**Uso en el proyecto:**
- `APIRouter` en cada dominio (`organizations/api.py`, `users/api.py`, `auth/api.py`).
- `Depends()` para inyectar sesiones de BD, services, y el usuario autenticado (`get_db`, `get_user_service`, `get_current_user`).
- `HTTPException` para traducir excepciones internas (`UserAlreadyExistsError`, `InvalidCredentialsError`, etc.) a respuestas HTTP con el status code correcto.
- `Query` para parámetros de paginación (`skip`, `limit`) con validación (`le=100`).
- `OAuth2PasswordBearer` para extraer el token JWT del header `Authorization` en cada request protegido.
- **Inyección y control de acceso robusto:**
  - `require_owner(current_user)`: Asegura privilegios exclusivos de propietario.
  - `require_admin(current_user)`: Permite operaciones de administración local a usuarios con rol `ADMIN` u `OWNER`.
  - `verify_same_organization(admin, target_organization_id)`: Restringe acciones dentro del contexto de la misma organización, permitiendo bypass automático solo a administradores de plataforma globales (`is_platform_admin`).
  - `can_manage_other_user(actor, target)`: Compara niveles de la jerarquía de roles (`ROLE_HIERARCHY`) para autorizar modificaciones de usuarios.

### `uvicorn`
Servidor ASGI que corre la aplicación FastAPI. Se usa con `--reload` en desarrollo para recargar automáticamente al detectar cambios en el código.

---

## 2. Base de datos y ORM

### `sqlalchemy` (v2.0, sintaxis async)
ORM usado para modelar las tablas (`Organization`, `User`) y ejecutar queries. Se usa la sintaxis moderna (`select()`, no `.query()`) junto con soporte async.

**Uso en el proyecto:**
- Modelos con `Column` e indexación avanzada, UUID como primary key, `relationship()` bidireccional entre `Organization` y `User`, y `TYPE_CHECKING` para evitar imports circulares.
- `create_async_engine`, `async_sessionmaker` con `expire_on_commit=False` para el manejo de sesiones async.
- **Transacciones complejas y atómicas:**
  - Creación conjunta de organización y su primer `OWNER` mediante un único `session.commit()` en `OrganizationService.create_with_owner`.
  - Soporte para **bloqueo de filas** (row-level locking) usando `.with_for_update()` en `UserRepository.get_by_id`. Esto asegura que durante la transferencia de propiedad, ambos usuarios queden bloqueados en la base de datos hasta que finalice la transacción, previniendo condiciones de carrera.

### `asyncpg`
Driver de PostgreSQL para SQLAlchemy en modo async. Es el que permite que `create_async_engine` funcione con Postgres de forma no bloqueante.

### `alembic`
Herramienta de migraciones de base de datos. Se configuró con un driver síncrono aparte porque Alembic todavía no soporta drivers async de forma nativa en su configuración estándar.

**Uso en el proyecto:** generación y aplicación de migraciones para las tablas `organizations` y `users`.
- **Restricciones complejas aplicadas vía Alembic:**
  - `ix_users_email_unique_active`: Índice único parcial para correos electrónicos (`email`) que aplica solo a los registros activos (`deleted_at IS NULL`). Permite que un correo sea reutilizado si la cuenta anterior ha sido borrada lógicamente.
  - `ix_users_one_active_owner_per_organization`: Índice único parcial que restringe la existencia de máximo un usuario con rol `OWNER` activo (`deleted_at IS NULL`) por cada `organization_id`.

---

## 3. Validación de datos

### `pydantic` (v2)
Define los schemas de entrada/salida de cada endpoint y valida automáticamente los datos que llegan en cada request.

**Uso en el proyecto:**
- Modelos avanzados de negocio:
  - `OrganizationBootstrap`: Recibe los datos para la organización y de forma embebida los datos del usuario propietario inicial (`owner_email`, `owner_password`, `owner_full_name`).
  - `OwnershipTransfer`: Recibe el ID del usuario destino (`target_user_id`) para la transferencia de propiedad.
- Separación estricta entre schemas de entrada (`UserCreate`, con `password` plano) y de salida (`UserResponse`, sin `password` ni `hashed_password`) para nunca exponer credenciales.
- `model_dump(exclude_unset=True)` en updates parciales (PATCH), para no sobreescribir campos que el cliente no envió.
- `EmailStr` en los campos de email para validación de formato automática — requiere el paquete `email-validator` instalado aparte.

### `email-validator`
Dependencia adicional requerida por Pydantic para que `EmailStr` funcione de manera nativa.

### `pydantic-settings`
Extensión de Pydantic para manejar configuración vía variables de entorno de forma tipada y validada en `core/config.py`.

---

## 4. Seguridad

### `argon2-cffi`
Librería de hasheo de contraseñas de última generación, recomendada por OWASP. **Reemplazó por completo a `bcrypt`** en el proyecto, ofreciendo mayor resistencia contra ataques de fuerza bruta y hardware especializado.

**Uso en el proyecto:**
- `hash_password(password)` en `app/core/security.py`: hashea el password antes de guardarlo en la base de datos (columna `hashed_password`) usando la clase `PasswordHasher()` con parámetros de alta seguridad por defecto.
- `verify_password(plain_password, hashed_password)`: compara un password en texto plano contra el hash guardado, manejando de forma segura las excepciones de fallo en la verificación (`VerifyMismatchError`).

### `pyjwt`
Librería para generar y decodificar JSON Web Tokens (JWT) usada en la protección de endpoints y la sesión de usuario.

**Uso en el proyecto:**
- `create_access_token(data: dict)` en `core/security.py`: firma tokens usando `SECRET_KEY` y el algoritmo `HS256`.
- `decode_access_token(token: str)`: verifica firma y expiración con `jwt.decode()`; lanza `InvalidTokenError` si el token expiró o es inválido.

---

## 5. Resumen de flujos principales y lógica de negocio

1. **Bootstrap de Organización** (`POST /organizations/`):
   Recibe `OrganizationBootstrap` → `OrganizationService.create_with_owner()`:
   - Verifica que el `tax_id` (si se provee) no esté duplicado.
   - Verifica que el email del propietario inicial no exista ya.
   - Crea la entidad `Organization`.
   - Registra al usuario con rol `OWNER`, hasheando su contraseña con `argon2-cffi`.
   - Guarda ambas entidades de forma **atómica** dentro de una misma transacción de base de datos.

2. **Registro de Usuarios** (`POST /users/`):
   Un administrador (`ADMIN` u `OWNER`) crea un nuevo usuario enviando `UserCreate`. El password se hashea con `argon2` y el usuario se asocia a la organización del administrador creador.

3. **Login de Usuarios** (`POST /auth/login`):
   Busca al usuario por email, valida la contraseña con `argon2` y retorna un JWT firmado que expira según la configuración (`ACCESS_TOKEN_EXPIRE_MINUTES`).

4. **Acceso Seguro y Jerarquía**:
   Cada endpoint seguro inyecta al `current_user` decodificando el JWT. Las operaciones se validan contra:
   - **Organización:** No se permite interactuar con usuarios de otra organización (`verify_same_organization`), a menos que el usuario sea administrador global de la plataforma (`is_platform_admin`).
   - **Jerarquía de Roles (`ROLE_HIERARCHY`):** Un usuario solo puede actualizar o eliminar a otro si su rol es estrictamente mayor en la jerarquía (`OWNER (3) > ADMIN (2) > MEMBER (1) > VIEWER (0)`).
   - **Eliminación del Owner:** No se permite que un `OWNER` elimine su propia cuenta sin antes transferir la propiedad de la organización.

5. **Transferencia de Propiedad** (`POST /organizations/{id}/transfer-ownership`):
   El propietario actual invoca este flujo enviando el `target_user_id` (quien debe pertenecer a la misma organización):
   - Se bloquean concurrentemente ambos registros de usuario en la base de datos con `for_update=True` para evitar modificaciones concurrentes.
   - Se degrada al propietario actual a `ADMIN`.
   - Se promueve al nuevo usuario a `OWNER`.
   - La transacción se confirma de forma atómica para no violar el índice de propietario único de Alembic (`ix_users_one_active_owner_per_organization`).

---

## 6. Pendientes relacionados con dependencias

- Mantener `requirements.txt` actualizado y congelado con las dependencias exactas del entorno de ejecución (ya sincronizado con `argon2-cffi`).
- Fases de Testing/CI (pendiente de instalación e integración):
  - `pytest`, `pytest-asyncio` para la ejecución de pruebas síncronas y asíncronas.
  - `testcontainers` o similar para pruebas de integración con base de datos real en un contenedor PostgreSQL.
  - `ruff` para linting y formateo rápido de código Python.
  - `mypy` para análisis estático y validación estricta del tipado en todo el proyecto.
