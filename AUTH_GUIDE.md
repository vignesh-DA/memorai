# 🔐 Authentication System - Quick Start

## Setup

1. **Install dependencies:**
   ```powershell
   .\scripts\setup_auth.ps1
   ```
   This installs auth packages and generates a secure JWT secret key.

2. **Restart server:**
   ```powershell
   uvicorn app.main:app --reload
   ```

---

## Usage

### 1. Register a New User

```bash
POST /api/v1/auth/register
Content-Type: application/json

{
  "email": "vignesh@example.com",
  "password": "SecurePass123!",
  "full_name": "Vignesh Kumar"
}
```

**Response:**
```json
{
  "user_id": "vignesh",
  "email": "vignesh@example.com",
  "full_name": "Vignesh Kumar",
  "is_active": true,
  "tier": "free"
}
```

---

### 2. Login (Get JWT Token)

```bash
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "vignesh@example.com",
  "password": "SecurePass123!"
}
```

**Response:**
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "token_type": "bearer",
  "expires_in": 604800
}
```

---

### 3. Use JWT Token in Requests

**Option A: Authorization Header (Web Apps)**
```bash
GET /api/v1/memories/list
Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
```

---

### 4. Create API Key (For Scripts/Integrations)

```bash
POST /api/v1/auth/api-keys
Authorization: Bearer <your_jwt_token>
Content-Type: application/json

{
  "name": "Production Server",
  "expires_days": 365
}
```

**Response:**
```json
{
  "key_id": "550e8400-e29b-41d4-a716-446655440000",
  "api_key": "sk_abc123xyz789...",  ⚠️ SAVE THIS - SHOWN ONCE!
  "key_prefix": "sk_abc123xyz789...",
  "name": "Production Server",
  "created_at": "2026-02-09T10:30:00",
  "expires_at": "2027-02-09T10:30:00",
  "warning": "Save this key - it won't be shown again!"
}
```

---

### 5. Use API Key in Requests

**Option B: X-API-Key Header (Scripts/CLI)**
```bash
GET /api/v1/memories/list
X-API-Key: sk_abc123xyz789...
```

---

## Authentication Methods Comparison

| Method | Use Case | Example |
|--------|----------|---------|
| **JWT Token** | Web apps, mobile apps | `Authorization: Bearer eyJ...` |
| **API Key** | Scripts, integrations, CLI tools | `X-API-Key: sk_abc...` |

Both methods work simultaneously - use whichever fits your use case!

---

## User Tiers & Quotas

| Tier | Max Memories | Requests/Day |
|------|--------------|--------------|
| **Free** | 10,000 | 1,000 |
| **Pro** | 100,000 | 10,000 |
| **Enterprise** | Unlimited | Unlimited |

---

## API Endpoints

### Authentication
- `POST /api/v1/auth/register` - Register user
- `POST /api/v1/auth/login` - Login (get JWT)
- `POST /api/v1/auth/refresh` - Refresh JWT token
- `GET /api/v1/auth/me` - Get current user info

### API Keys
- `POST /api/v1/auth/api-keys` - Create new API key
- `GET /api/v1/auth/api-keys` - List your API keys
- `DELETE /api/v1/auth/api-keys/{key_id}` - Revoke API key

---

## Security Best Practices

1. **✅ JWT Secret Key:** Generated with `openssl rand -hex 32` (256-bit)
2. **✅ Password Hashing:** Bcrypt with automatic salt
3. **✅ API Key Hashing:** SHA-256 (never stored in plain text)
4. **✅ Token Expiration:** 7 days access + 30 days refresh
5. **✅ User Isolation:** All queries filtered by user_id

---

## Migration from "demo_user"

**Old way (everyone shared demo_user):**
```bash
POST /api/v1/conversation
{
  "user_id": "demo_user",  ❌ No auth, anyone can access
  "user_message": "Hello"
}
```

**New way (authenticated):**
```bash
POST /api/v1/conversation
Authorization: Bearer <your_token>
# user_id automatically extracted from token ✅
{
  "user_message": "Hello"
}
```

---

## Example: Full Workflow

```bash
# 1. Register
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"YourPass123!","full_name":"Your Name"}'

# 2. Login
TOKEN=$(curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"YourPass123!"}' \
  | jq -r '.access_token')

# 3. Use token in memory requests
curl -X POST http://localhost:8000/api/v1/conversation \  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_message":"Remember, my name is Vignesh"}'

# 4. Create API key for scripts
curl -X POST http://localhost:8000/api/v1/auth/api-keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"My Script","expires_days":365}'

# 5. Use API key (save from step 4)
curl -X GET http://localhost:8000/api/v1/memories/demo_user/list \
  -H "X-API-Key: sk_your_key_here"
```

---

## Troubleshooting

**"Invalid authentication credentials"**
- Check token hasn't expired (7 days)
- Verify Authorization header format: `Bearer <token>`
- Try refreshing token with `/auth/refresh`

**"Incorrect email or password"**
- Passwords are case-sensitive
- Minimum 8 characters required

**"API key not found"**
- API keys expire if set
- Check key hasn't been revoked
- Generate new key if lost (old ones not recoverable)

---

## 🎯 Ready to Scale

Your system now supports:
✅ **Multi-user isolation** - Each user's memories are private  
✅ **Secure authentication** - JWT + API Keys  
✅ **Rate limiting ready** - User quotas in place  
✅ **Production-grade auth** - Bcrypt + SHA-256 hashing  
✅ **Flexible access** - Web apps (JWT) + Scripts (API keys)  

Next: Add rate limiting middleware and you're production-ready! 🚀
