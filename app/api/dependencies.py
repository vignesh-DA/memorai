"""
FastAPI Authentication Dependencies
Protect endpoints with JWT or API Key
"""

import logging
from typing import Optional
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from app.services.auth_service import AuthService
from app.models.auth import User, TokenData
from app.database import db_manager

logger = logging.getLogger(__name__)

# Security schemes
bearer_scheme = HTTPBearer(auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_auth_service() -> AuthService:
    """Get auth service instance"""
    return AuthService(engine=db_manager._engine)


async def get_current_user_from_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    auth_service: AuthService = Depends(get_auth_service)
) -> Optional[User]:
    """Get current user from JWT token"""
    if not credentials:
        return None
    
    token = credentials.credentials
    token_data = auth_service.verify_token(token)
    
    if not token_data:
        logger.warning("Invalid or expired JWT token")
        return None
    
    user = await auth_service.get_user_by_id(token_data.user_id)
    if not user:
        logger.warning(f"User not found for token user_id: {token_data.user_id}")
    return user


async def get_current_user_from_api_key(
    api_key: Optional[str] = Security(api_key_scheme),
    auth_service: AuthService = Depends(get_auth_service)
) -> Optional[User]:
    """Get current user from API key"""
    if not api_key:
        return None
    
    user_id = await auth_service.validate_api_key(api_key)
    
    if not user_id:
        return None
    
    user = await auth_service.get_user_by_id(user_id)
    return user


async def get_current_user(
    user_from_token: Optional[User] = Depends(get_current_user_from_token),
    user_from_api_key: Optional[User] = Depends(get_current_user_from_api_key),
) -> User:
    """Get current user from either JWT token or API key.
    
    Tries JWT first, then API key.
    Raises 401 if neither provided or invalid.
    """
    user = user_from_token or user_from_api_key
    
    if not user:
        logger.warning("Authentication failed: no valid token or API key provided")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please provide a valid Bearer token or API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account"
        )
    
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Get current active user (alias for clarity)"""
    return current_user


def require_pro_tier(current_user: User = Depends(get_current_user)) -> User:
    """Require pro or enterprise tier"""
    if current_user.tier not in ["pro", "enterprise"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature requires a Pro or Enterprise subscription"
        )
    return current_user


def require_enterprise_tier(current_user: User = Depends(get_current_user)) -> User:
    """Require enterprise tier"""
    if current_user.tier != "enterprise":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature requires an Enterprise subscription"
        )
    return current_user
