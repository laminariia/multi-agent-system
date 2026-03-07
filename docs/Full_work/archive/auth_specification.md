# 🔐 Authentication & Authorization — Multi-Agent System

**Version:** 1.0  
**Framework:** Remix Auth (Dashboard) + Litestar JWT (API)

---

## 🏗️ Auth Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           AUTHENTICATION FLOW                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐   │
│  │  Dashboard  │───▶│  Remix Auth │───▶│  Litestar   │───▶│  PostgreSQL │   │
│  │  (Remix)   │    │  Session    │    │  JWT Verify │    │  users      │   │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘   │
│                                                                              │
│  ┌─────────────┐    ┌─────────────┐                                          │
│  │  Telegram   │───▶│  Bot Token  │── verify chat_id ──▶ users.telegram_id  │
│  │    Bot      │    │  + chat_id  │                                          │
│  └─────────────┘    └─────────────┘                                          │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 👤 User Roles (RBAC)

| Role | Permissions |
|------|-------------|
| **owner** | Full access: HITL, agents, projects, settings, analytics |
| **viewer** | Read-only: dashboard, analytics, logs (no HITL actions) |

### Permission Matrix

| Action | owner | viewer |
|--------|-------|--------|
| View Dashboard | ✅ | ✅ |
| View Analytics | ✅ | ✅ |
| View Agent Logs | ✅ | ✅ |
| Resolve HITL | ✅ | ❌ |
| Restart Agents | ✅ | ❌ |
| Modify Projects | ✅ | ❌ |
| Start Geo Scan | ✅ | ❌ |
| Manage Campaigns | ✅ | ❌ |
| Change Settings | ✅ | ❌ |
| Manage Users | ✅ | ❌ |

---

## 🔑 Authentication Methods

### 1. Email + Password (Primary)

```python
from litestar import Controller, post, get
from litestar.security.jwt import JWTAuth, Token
from litestar.connection import ASGIConnection
from litestar.middleware.session.server_side import ServerSideSessionConfig
import bcrypt
from datetime import datetime, timedelta

async def retrieve_user_handler(token: Token, connection: ASGIConnection) -> User | None:
    """Retrieve user from JWT token."""
    user = await UserRepository.get_by_id(token.sub)
    return user

jwt_auth = JWTAuth[User](
    retrieve_user_handler=retrieve_user_handler,
    token_secret=os.environ["JWT_SECRET_KEY"],
    default_token_expiration=timedelta(hours=24),
)
```

> **Required env var:** `JWT_SECRET_KEY` must be set. Generate a strong random key
> (e.g., `openssl rand -hex 32`) and store it securely. Never commit it to source control.

### 2. Telegram Auth (Optional)

For linking Telegram account to dashboard:

```
1. User clicks "Connect Telegram" in Settings
2. Bot sends verification code to user's Telegram
3. User enters code in Dashboard
4. chat_id stored in users table
```

---

## 🎫 JWT Token Structure

### Access Token (15 min expiry)
```json
{
  "sub": "user-uuid",
  "email": "user@example.com",
  "role": "owner",
  "iat": 1706560000,
  "exp": 1706560900
}
```

### Refresh Token (7 days expiry)
```json
{
  "sub": "user-uuid",
  "type": "refresh",
  "iat": 1706560000,
  "exp": 1707164800
}
```

---

## 🛡️ API Security

### Litestar JWT Auth + Guards

```python
from litestar import get, post, Controller
from litestar.connection import ASGIConnection
from litestar.handlers import BaseRouteHandler
from litestar.security.jwt import JWTAuth, Token
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException

async def retrieve_user_handler(token: Token, connection: ASGIConnection) -> User | None:
    """Retrieve user from JWT token. Returns None if user not found (triggers 401)."""
    user = await UserRepository.get_by_id(token.sub)
    return user

jwt_auth = JWTAuth[User](
    retrieve_user_handler=retrieve_user_handler,
    token_secret=os.environ["JWT_SECRET_KEY"],
    default_token_expiration=timedelta(hours=24),
)

def require_role(role: str):
    """Litestar guard that checks user role."""
    async def guard(connection: ASGIConnection, handler: BaseRouteHandler) -> None:
        if not connection.user or role not in connection.user.roles:
            raise PermissionDeniedException("Insufficient permissions")
    return guard

# Usage — guards are passed as route handler parameters
class HITLController(Controller):
    path = "/api/hitl"

    @post("/{hitl_id:uuid}/resolve", guards=[require_role("owner")])
    async def resolve_hitl(self, hitl_id: UUID) -> dict:
        ...
```

---

## 📱 Telegram Bot Authentication

### Verification Flow

```python
class TelegramAuth:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
    
    async def verify_update(self, update: dict) -> bool:
        """Verify update is from Telegram."""
        # Telegram updates are verified by secret webhook URL
        return True
    
    async def get_user_by_chat_id(self, chat_id: int) -> Optional[User]:
        """Find user linked to this Telegram chat."""
        return await db.query(User).filter(
            User.telegram_chat_id == chat_id
        ).first()
    
    async def handle_hitl_action(
        self, 
        chat_id: int, 
        hitl_id: str, 
        action: str
    ) -> dict:
        user = await self.get_user_by_chat_id(chat_id)
        if not user:
            return {"error": "Telegram not linked to any account"}
        
        if user.role != "owner":
            return {"error": "No permission to resolve HITL"}
        
        return await resolve_hitl(hitl_id, action, user.id)
```

---

## 🔒 Security Measures

### 1. Password Hashing
```python
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)
```

### 2. Rate Limiting (Litestar built-in)
```python
from litestar.middleware.rate_limit import RateLimitConfig

rate_limit_config = RateLimitConfig(
    rate_limit=("minute", 60),
    exclude=["/api/health"],
)

# For stricter limits on auth endpoints, apply per-route config:
auth_rate_limit = RateLimitConfig(
    rate_limit=("minute", 5),
)

# Pass rate_limit_config to the Litestar app constructor:
# app = Litestar(..., middleware=[rate_limit_config.middleware])
```

### 3. CORS Configuration (Litestar CORSConfig)
```python
from litestar.config.cors import CORSConfig

cors_config = CORSConfig(
    allow_origins=["https://dashboard.yourdomain.com"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    allow_credentials=True,
)

# Pass cors_config to the Litestar app constructor:
# app = Litestar(..., cors_config=cors_config)
```

### 4. Secrets Management
```python
# Environment variables (never commit!)
SECRET_KEY = os.environ["JWT_SECRET_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# For production: use AWS Secrets Manager / HashiCorp Vault
```

> **Required environment variable:** `JWT_SECRET_KEY` is mandatory for the application to start.
> Generate it with: `openssl rand -hex 32`
> This key signs all JWT tokens. If rotated, all existing sessions will be invalidated.
> Store it in `.env` (local) or a secrets manager (production). Never commit it to version control.

---

## 🚀 Initial Setup

### First User Creation

Since there's no registration page, create first user via CLI:

```bash
python -m src.cli.create_user \
  --email "admin@example.com" \
  --password "secure-password" \
  --role "owner" \
  --name "Admin"
```

### Add Telegram Integration

```bash
python -m src.cli.link_telegram \
  --user-email "admin@example.com" \
  --chat-id 123456789
```

---

## 📋 Session Management

### Active Sessions Table (Optional)
```sql
CREATE TABLE user_sessions (
    id              UUID PRIMARY KEY,
    user_id         UUID REFERENCES users(id),
    refresh_token   VARCHAR(255) UNIQUE,
    device_info     JSONB,
    ip_address      INET,
    created_at      TIMESTAMP WITH TIME ZONE,
    expires_at      TIMESTAMP WITH TIME ZONE,
    revoked         BOOLEAN DEFAULT FALSE
);
```

### Logout All Devices
```python
from litestar import post, Request
from litestar.security.jwt import JWTAuth

class AuthController(Controller):
    path = "/api/auth"

    @post("/logout-all")
    async def logout_all_devices(self, request: Request) -> dict:
        user = request.user
        await db.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id)
            .values(revoked=True)
        )
        return {"message": "All sessions revoked"}
```
