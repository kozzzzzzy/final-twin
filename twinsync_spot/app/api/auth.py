"""API Authentication for TwinSync Spot Mobile App.

Provides token-based authentication for external API access.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateTokenRequest(BaseModel):
    """Request to create a new API token."""
    name: str


class CreateTokenResponse(BaseModel):
    """Response with new API token."""
    token: str
    name: str
    message: str


class TokenInfo(BaseModel):
    """Information about a token (without the actual token value)."""
    id: int
    name: str
    created_at: str
    last_used: Optional[str]
    is_active: bool


class TokenListResponse(BaseModel):
    """Response with list of tokens."""
    tokens: list[TokenInfo]


class VerifyTokenResponse(BaseModel):
    """Response for token verification."""
    valid: bool
    message: str


@router.post("/auth/token", response_model=CreateTokenResponse)
async def create_token(request: Request, data: CreateTokenRequest):
    """Create a new API token for mobile app access.
    
    This token can be used to authenticate API requests from external apps.
    Store the returned token securely - it won't be shown again!
    """
    db = request.app.state.db
    
    if not data.name or len(data.name.strip()) < 1:
        raise HTTPException(status_code=400, detail="Token name is required")
    
    name = data.name.strip()[:50]  # Limit name length
    
    try:
        token = await db.create_api_token(name)
        
        return CreateTokenResponse(
            token=token,
            name=name,
            message="Token created. Save this token securely - it won't be shown again!"
        )
    except Exception as e:
        logger.error(f"Error creating API token: {e}")
        raise HTTPException(status_code=500, detail="Failed to create token")


@router.post("/auth/verify", response_model=VerifyTokenResponse)
async def verify_token(
    request: Request,
    authorization: Optional[str] = Header(None)
):
    """Verify that an API token is valid.
    
    Pass the token in the Authorization header as 'Bearer <token>'.
    """
    db = request.app.state.db
    
    if not authorization:
        return VerifyTokenResponse(
            valid=False,
            message="No authorization header provided"
        )
    
    # Extract token from "Bearer <token>"
    if not authorization.startswith("Bearer "):
        return VerifyTokenResponse(
            valid=False,
            message="Invalid authorization header format. Use 'Bearer <token>'"
        )
    
    token = authorization[7:].strip()
    
    if not token:
        return VerifyTokenResponse(
            valid=False,
            message="No token provided"
        )
    
    try:
        is_valid = await db.verify_api_token(token)
        
        return VerifyTokenResponse(
            valid=is_valid,
            message="Token is valid" if is_valid else "Token is invalid or revoked"
        )
    except Exception as e:
        logger.error(f"Error verifying token: {e}")
        return VerifyTokenResponse(
            valid=False,
            message="Error verifying token"
        )


@router.get("/auth/tokens", response_model=TokenListResponse)
async def list_tokens(request: Request):
    """List all API tokens (without exposing token values).
    
    Returns information about all created tokens for management purposes.
    """
    db = request.app.state.db
    
    try:
        tokens = await db.list_api_tokens()
        
        return TokenListResponse(
            tokens=[
                TokenInfo(
                    id=t["id"],
                    name=t["name"],
                    created_at=t["created_at"],
                    last_used=t["last_used"],
                    is_active=t["is_active"]
                )
                for t in tokens
            ]
        )
    except Exception as e:
        logger.error(f"Error listing tokens: {e}")
        raise HTTPException(status_code=500, detail="Failed to list tokens")


@router.delete("/auth/tokens/{token_id}")
async def revoke_token(request: Request, token_id: int):
    """Revoke (disable) an API token.
    
    The token will no longer be valid for authentication.
    """
    db = request.app.state.db
    
    try:
        success = await db.revoke_api_token(token_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Token not found")
        
        return {"message": "Token revoked", "token_id": token_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error revoking token: {e}")
        raise HTTPException(status_code=500, detail="Failed to revoke token")


async def verify_api_token_dependency(
    request: Request,
    authorization: Optional[str] = Header(None)
) -> bool:
    """FastAPI dependency to verify API token.
    
    Use this as a dependency in routes that require API authentication.
    
    Usage:
        @router.get("/protected")
        async def protected_route(request: Request, _=Depends(verify_api_token_dependency)):
            ...
    """
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header format",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    token = authorization[7:].strip()
    
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Token required",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    db = request.app.state.db
    
    try:
        is_valid = await db.verify_api_token(token)
        
        if not is_valid:
            raise HTTPException(
                status_code=401,
                detail="Invalid or revoked token",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        return True
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in token verification dependency: {e}")
        raise HTTPException(
            status_code=500,
            detail="Authentication error"
        )
