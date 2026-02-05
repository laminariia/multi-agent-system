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

```typescript
// NextAuth configuration
export const authOptions: NextAuthOptions = {
  providers: [
    CredentialsProvider({
      name: "Credentials",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" }
      },
      async authorize(credentials) {
        const user = await verifyCredentials(
          credentials.email, 
          credentials.password
        );
        return user;
      }
    })
  ],
  session: {
    strategy: "jwt",
    maxAge: 7 * 24 * 60 * 60, // 7 days
  },
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.role = user.role;
        token.userId = user.id;
      }
      return token;
    },
    async session({ session, token }) {
      session.user.role = token.role;
      session.user.id = token.userId;
      return session;
    }
  }
};
```

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

### Litestar Dependency

```python
from litestar import get, post
from litestar.connection import ASGIConnection
from litestar.middleware import AbstractAuthenticationMiddleware
from litestar.exceptions import NotAuthorizedException

class JWTAuthMiddleware(AbstractAuthenticationMiddleware):

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security)
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user = await get_user_by_id(payload["sub"])
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

def require_role(required_role: str):
    async def role_checker(user: User = Depends(get_current_user)):
        if user.role != required_role and user.role != "owner":
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return role_checker

# Usage
@app.post("/api/hitl/{id}/resolve")
async def resolve_hitl(
    id: UUID,
    user: User = Depends(require_role("owner"))
):
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

### 2. Rate Limiting
```python
from slowapi import Limiter

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/auth/login")
@limiter.limit("5/minute")
async def login(request: Request, credentials: LoginRequest):
    ...
```

### 3. CORS Configuration
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://dashboard.example.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 4. Secrets Management
```python
# Environment variables (never commit!)
SECRET_KEY = os.environ["JWT_SECRET_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# For production: use AWS Secrets Manager / HashiCorp Vault
```

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
@app.post("/api/auth/logout-all")
async def logout_all_devices(user: User = Depends(get_current_user)):
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id)
        .values(revoked=True)
    )
    return {"message": "All sessions revoked"}
```
