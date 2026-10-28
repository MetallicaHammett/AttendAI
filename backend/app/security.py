"""
Security utilities for authentication including password hashing and JWT token handling.
"""
from datetime import datetime, timedelta
from typing import Optional, Union
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app import schemas
from app.config import settings
from app.database import get_db

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES  # Token expiration time

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against its hash.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Generate a bcrypt hash of the provided password.
    """
    return pwd_context.hash(password)


def authenticate_user(db: Session, username: str, password: str):
    """
    Authenticate a user by username or email and password.
    """
    from app import crud
    # Try to get user by email first
    user = crud.get_user_by_email(db, email=username)
    if not user:
        # If not found by email, try by username
        user = crud.get_user_by_username(db, username=username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token with the provided data.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY.get_secret_value(), algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_active_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """
    Get the current active user from a JWT token.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY.get_secret_value(), algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = schemas.TokenData(username=username)
    except JWTError:
        raise credentials_exception
    from app import crud
    # Try to get user by username first (since token contains username)
    user = crud.get_user_by_username(db, username=token_data.username)
    if user is None:
        # If not found by username, try by email as fallback
        user = crud.get_user_by_email(db, email=token_data.username)
    if user is None or not user.is_active:
        raise credentials_exception
    return user