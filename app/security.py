"""Seguridad: hashing bcrypt, JWT (HS256), dependencias de auth y roles.

RNF-03 (auth) y RNF-04 (permisos por rol).
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from . import models

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="No autenticado o token inválido",
    headers={"WWW-Authenticate": "Bearer"},
)


# ----------------------------- Hashing ---------------------------------------
# bcrypt directo (passlib es incompatible con bcrypt>=4). Se trunca a 72 bytes
# porque bcrypt no acepta contraseñas más largas.
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))


# ----------------------------- Tokens ----------------------------------------
def _crear_token(data: dict, expira: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + expira
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def crear_access_token(usuario: models.Usuario) -> str:
    return _crear_token(
        {"sub": str(usuario.id), "rol": usuario.rol.nombre, "typ": "access"},
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def crear_refresh_token(usuario: models.Usuario) -> str:
    return _crear_token(
        {"sub": str(usuario.id), "typ": "refresh"},
        timedelta(days=settings.refresh_token_expire_days),
    )


# ----------------------------- Dependencias ----------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> models.Usuario:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("typ") != "access":
            raise _CREDENTIALS_EXC
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise _CREDENTIALS_EXC

    usuario = db.get(models.Usuario, user_id)
    if not usuario or usuario.estado.value != "activo":
        raise _CREDENTIALS_EXC
    return usuario


def require_role(roles: List[str]):
    """Dependencia de fábrica: valida que el usuario tenga uno de los roles."""
    roles_permitidos = set(roles)

    def _checker(usuario: models.Usuario = Depends(get_current_user)) -> models.Usuario:
        if usuario.rol.nombre not in roles_permitidos:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene permisos para esta acción",
            )
        return usuario

    return _checker
