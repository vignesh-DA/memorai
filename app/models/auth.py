"""
Authentication models for user management
"""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, EmailStr


class User(BaseModel):
    """User account model"""
    user_id: str = Field(..., description="Unique user identifier")
    email: EmailStr
    hashed_password: str
    full_name: Optional[str] = None
    is_active: bool = True
    is_verified: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None
    
    # Quotas for scaling
    max_memories: int = 10000
    max_requests_per_day: int = 1000
    tier: str = "free"  # free, pro, enterprise


class UserCreate(BaseModel):
    """User registration request"""
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    """User login request"""
    email: EmailStr
    password: str


class Token(BaseModel):
    """JWT token response"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class TokenData(BaseModel):
    """Token payload data"""
    user_id: str
    email: str
    tier: str = "free"
    exp: int  # expiration timestamp


class APIKey(BaseModel):
    """API Key model for programmatic access"""
    key_id: UUID = Field(default_factory=uuid4)
    user_id: str
    key_hash: str  # Hashed API key (never store plain)
    key_prefix: str  # First 8 chars for display (sk_vignesh_...)
    name: str = Field(..., description="Friendly name for the key")
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class APIKeyCreate(BaseModel):
    """API key creation request"""
    name: str = Field(..., description="Friendly name (e.g., 'Production Server')")
    expires_days: Optional[int] = None  # None = never expires


class APIKeyResponse(BaseModel):
    """API key creation response (only time plain key is shown)"""
    key_id: UUID
    api_key: str  # Plain key - SHOW ONCE
    key_prefix: str
    name: str
    created_at: datetime
    expires_at: Optional[datetime]
    warning: str = "Save this key - it won't be shown again!"
