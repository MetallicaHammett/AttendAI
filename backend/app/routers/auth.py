from fastapi import APIRouter, HTTPException, Depends, status, Form
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app import schemas, crud, security
from pydantic import BaseModel, Field

router = APIRouter()

# Use security module for password hashing and JWT handling
from app.security import (
    verify_password,
    get_password_hash,
    authenticate_user as security_authenticate_user,
    create_access_token as security_create_access_token
)

# HTTP bearer scheme for token extraction
http_bearer = HTTPBearer()

# Pydantic models
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str | None = None

class UserBase(BaseModel):
    email: str = Field(..., max_length=255, description="User email address")
    username: str = Field(..., max_length=50, description="Username")

class UserCreate(UserBase):
    password: str = Field(..., min_length=8, description="Password (minimum 8 characters)")

class User(UserBase):
    id: int
    is_active: bool = True
    created_at: schemas.UTCDateTime

    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    username: str
    password: str


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(http_bearer), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(credentials.credentials, settings.SECRET_KEY.get_secret_value(), algorithms=["HS256"])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError as e:
        raise credentials_exception
    from app import crud
    # Try to get user by username first (since token contains username)
    user = crud.get_user_by_username(db, username=token_data.username)
    if user is None:
        # If not found by username, try by email as fallback
        user = crud.get_user_by_email(db, email=token_data.username)
    # Deactivated accounts are rejected however they were looked up
    if user is None or not user.is_active:
        raise credentials_exception
    return schemas.UserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        is_active=user.is_active,
        role=user.role or "viewer",
        student_db_id=user.student_db_id,
        created_at=user.created_at,
        updated_at=user.updated_at
    )

@router.post("/token", response_model=Token)
@router.post("/login", response_model=Token)
async def login_for_access_token(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = security_authenticate_user(db, username, password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security_create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=schemas.UserResponse)
async def read_users_me(current_user: schemas.UserResponse = Depends(get_current_user)):
    return current_user

@router.get("/registration")
async def registration_status():
    """Public: whether self-registration is open (ALLOW_SELF_REGISTRATION), so the sign-in
    page only offers "Sign up" when it can succeed. Reveals nothing else."""
    return {"enabled": bool(settings.ALLOW_SELF_REGISTRATION)}


@router.post("/register", response_model=schemas.UserResponse)
async def create_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    """Self-registration. New accounts are always read-only "viewer" accounts; an admin
    grants more access. Disabled entirely when ALLOW_SELF_REGISTRATION is False."""
    if not settings.ALLOW_SELF_REGISTRATION:
        raise HTTPException(status_code=403, detail="Self-registration is disabled. Ask an administrator for an account.")
    # Uniqueness ignores letter case, so look-alikes such as "surya" next to "Surya" are refused
    conflict = crud.find_conflicting_user(db, username=user.username, email=user.email)
    if conflict == "email":
        raise HTTPException(status_code=400, detail="Email already registered")
    if conflict == "username":
        raise HTTPException(status_code=400, detail="Username already taken")

    # Create new user
    try:
        db_user = crud.create_user(db=db, user=user)
    except IntegrityError:  # a simultaneous request took the name first; the unique index is the final check
        db.rollback()
        raise HTTPException(status_code=400, detail="Username or email already registered")
    return schemas.UserResponse(
        id=db_user.id,
        email=db_user.email,
        username=db_user.username,
        is_active=db_user.is_active,
        role=db_user.role or "viewer",
        created_at=db_user.created_at,
        updated_at=db_user.updated_at
    )