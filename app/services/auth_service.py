"""
Authentication Service - JWT + API Keys
Handles user registration, login, and API key management
"""

import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy import text

from app.models.auth import (
    User, UserCreate, UserLogin, Token, TokenData, 
    APIKey, APIKeyCreate, APIKeyResponse
)
from app.config import get_settings

settings = get_settings()

# Password hashing - configure bcrypt to handle long passwords
pwd_context = CryptContext(
    schemes=["bcrypt"], 
    deprecated="auto",
    bcrypt__rounds=12,
    bcrypt__ident="2b",
    bcrypt__truncate_error=False  # Silently truncate passwords over 72 bytes
)

# JWT settings
SECRET_KEY = settings.jwt_secret_key  # Add to .env
ALGORITHM = "HS256"


class AuthService:
    """Handle authentication and authorization"""
    
    # Token expiration settings
    ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
    REFRESH_TOKEN_EXPIRE_DAYS = 30
    
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
    
    async def initialize_tables(self):
        """Create auth tables if not exist"""
        async with self.engine.begin() as conn:
            # Users table
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id VARCHAR(255) PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    hashed_password TEXT NOT NULL,
                    full_name VARCHAR(255),
                    is_active BOOLEAN DEFAULT TRUE,
                    is_verified BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_login TIMESTAMP,
                    max_memories INTEGER DEFAULT 10000,
                    max_requests_per_day INTEGER DEFAULT 1000,
                    tier VARCHAR(20) DEFAULT 'free'
                )
            """))
            
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)
            """))
            
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active)
            """))
            
            # API Keys table
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_id UUID PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    key_hash TEXT NOT NULL,
                    key_prefix VARCHAR(20) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_used TIMESTAMP,
                    expires_at TIMESTAMP
                )
            """))
            
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id)
            """))
            
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)
            """))
            
            # Rate limiting table
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    user_id VARCHAR(255) PRIMARY KEY,
                    requests_today INTEGER DEFAULT 0,
                    last_reset DATE NOT NULL DEFAULT CURRENT_DATE
                )
            """))
    
    # Password methods
    def hash_password(self, password: str) -> str:
        """Hash a password (truncate to 72 bytes for bcrypt)"""
        # Truncate to 72 bytes (bcrypt limit)
        if len(password.encode("utf-8")) > 72:
            password = password.encode("utf-8")[:72].decode("utf-8", errors="ignore")
        return pwd_context.hash(password)
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against hash (truncate to 72 bytes for bcrypt)"""
        if len(plain_password.encode("utf-8")) > 72:
            plain_password = plain_password.encode("utf-8")[:72].decode("utf-8", errors="ignore")
        return pwd_context.verify(plain_password, hashed_password)
    
    # JWT Token methods
    def create_access_token(self, user: User) -> str:
        """Create JWT access token"""
        expire = datetime.utcnow() + timedelta(minutes=self.ACCESS_TOKEN_EXPIRE_MINUTES)
        payload = {
            "user_id": user.user_id,
            "email": user.email,
            "tier": user.tier,
            "exp": int(expire.timestamp()),
            "type": "access"
        }
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    def create_refresh_token(self, user: User) -> str:
        """Create JWT refresh token"""
        expire = datetime.utcnow() + timedelta(days=self.REFRESH_TOKEN_EXPIRE_DAYS)
        payload = {
            "user_id": user.user_id,
            "exp": int(expire.timestamp()),
            "type": "refresh"
        }
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    def verify_token(self, token: str) -> Optional[TokenData]:
        """Verify and decode JWT token"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return TokenData(
                user_id=payload["user_id"],
                email=payload.get("email", ""),
                tier=payload.get("tier", "free"),
                exp=payload["exp"]
            )
        except JWTError:
            return None
    
    # API Key methods
    def generate_api_key(self) -> Tuple[str, str, str]:
        """Generate API key and its hash.
        
        Returns:
            (plain_key, key_hash, key_prefix)
        """
        # Format: sk_{user_prefix}_{random}
        random_part = secrets.token_urlsafe(32)
        plain_key = f"sk_{random_part}"
        
        # Hash for storage
        key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
        
        # Prefix for display
        key_prefix = plain_key[:16] + "..."
        
        return plain_key, key_hash, key_prefix
    
    def hash_api_key(self, api_key: str) -> str:
        """Hash API key for lookup"""
        return hashlib.sha256(api_key.encode()).hexdigest()
    
    # User management
    async def create_user(self, user_data: UserCreate) -> User:
        """Register new user"""
        async with self.engine.begin() as conn:
            # Check if email exists
            result = await conn.execute(
                text("SELECT user_id FROM users WHERE email = :email"),
                {"email": user_data.email}
            )
            existing = result.mappings().first()
            if existing:
                raise ValueError("Email already registered")
            
            # Generate user_id from email
            user_id = user_data.email.split("@")[0].lower()
            
            # Handle duplicates
            result = await conn.execute(
                text("SELECT COUNT(*) FROM users WHERE user_id LIKE :pattern"),
                {"pattern": f"{user_id}%"}
            )
            count = result.scalar() or 0
            if count > 0:
                user_id = f"{user_id}_{count + 1}"
            
            # Hash password
            hashed_pw = self.hash_password(user_data.password)
            
            # Insert user
            await conn.execute(
                text("""
                    INSERT INTO users (user_id, email, hashed_password, full_name)
                    VALUES (:user_id, :email, :hashed_password, :full_name)
                """),
                {"user_id": user_id, "email": user_data.email, 
                 "hashed_password": hashed_pw, "full_name": user_data.full_name}
            )
            
            # Return user object
            return User(
                user_id=user_id,
                email=user_data.email,
                hashed_password=hashed_pw,
                full_name=user_data.full_name
            )
    
    async def authenticate_user(self, login: UserLogin) -> Optional[User]:
        """Authenticate user with email/password"""
        async with self.engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT user_id, email, hashed_password, full_name, is_active, 
                       is_verified, created_at, updated_at, last_login,
                       max_memories, max_requests_per_day, tier
                FROM users WHERE email = :email
            """), {"email": login.email})
            
            row = result.mappings().first()
            
            if not row:
                return None
            
            # Verify password
            if not self.verify_password(login.password, row["hashed_password"]):
                return None
            
            # Update last login  
            async with self.engine.begin() as update_conn:
                await update_conn.execute(
                    text("UPDATE users SET last_login = NOW() WHERE user_id = :user_id"),
                    {"user_id": row["user_id"]}
                )
            
            return User(**dict(row))
    
    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        """Get user by ID"""
        async with self.engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT user_id, email, hashed_password, full_name, is_active,
                       is_verified, created_at, updated_at, last_login,
                       max_memories, max_requests_per_day, tier
                FROM users WHERE user_id = :user_id
            """), {"user_id": user_id})
            
            row = result.mappings().first()
            if row:
                return User(**dict(row))
            return None
    
    # API Key management
    async def create_api_key(
        self, 
        user_id: str, 
        key_data: APIKeyCreate
    ) -> APIKeyResponse:
        """Create new API key for user"""
        async with self.engine.begin() as conn:
            # Generate key
            plain_key, key_hash, key_prefix = self.generate_api_key()
            
            # Calculate expiration
            expires_at = None
            if key_data.expires_days:
                expires_at = datetime.utcnow() + timedelta(days=key_data.expires_days)
            
            # Store in database
            from uuid import uuid4
            key_id = uuid4()
            
            await conn.execute(
                text("""
                    INSERT INTO api_keys (
                        key_id, user_id, key_hash, key_prefix, name, expires_at
                    ) VALUES (:key_id, :user_id, :key_hash, :key_prefix, :name, :expires_at)
                """),
                {"key_id": str(key_id), "user_id": user_id, "key_hash": key_hash, 
                 "key_prefix": key_prefix, "name": key_data.name, "expires_at": expires_at}
            )
            
            return APIKeyResponse(
                key_id=key_id,
                api_key=plain_key,  # ONLY time plain key is returned
                key_prefix=key_prefix,
                name=key_data.name,
                created_at=datetime.utcnow(),
                expires_at=expires_at
            )
    
    async def validate_api_key(self, api_key: str) -> Optional[str]:
        """Validate API key and return user_id.
        
        Returns:
            user_id if valid, None otherwise
        """
        key_hash = self.hash_api_key(api_key)
        
        async with self.engine.connect() as conn:
            result = await conn.execute(
                text("""
                    SELECT user_id, is_active, expires_at
                    FROM api_keys
                    WHERE key_hash = :key_hash
                """),
                {"key_hash": key_hash}
            )
            
            row = result.mappings().first()
            if not row:
                return None
            
            # Check if active
            if not row["is_active"]:
                return None
            
            # Check expiration
            if row["expires_at"] and row["expires_at"] < datetime.utcnow():
                return None
            
            # Update last used
            async with self.engine.begin() as update_conn:
                await update_conn.execute(
                    text("""
                        UPDATE api_keys
                        SET last_used = NOW()
                        WHERE key_hash = :key_hash
                    """),
                    {"key_hash": key_hash}
                )
            
            return row["user_id"]
    
    async def list_api_keys(self, user_id: str):
        """List user's API keys (without revealing keys)"""
        async with self.engine.connect() as conn:
            result = await conn.execute(
                text("""
                    SELECT key_id, key_prefix, name, is_active, created_at, last_used, expires_at
                    FROM api_keys
                    WHERE user_id = :user_id
                    ORDER BY created_at DESC
                """),
                {"user_id": user_id}
            )
            
            return [dict(row._asdict()) for row in result.fetchall()]
    
    async def revoke_api_key(self, user_id: str, key_id: str) -> bool:
        """Revoke an API key"""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text("""
                    UPDATE api_keys
                    SET is_active = FALSE
                    WHERE key_id = :key_id AND user_id = :user_id
                """),
                {"key_id": key_id, "user_id": user_id}
            )
            
            return result.rowcount == 1
