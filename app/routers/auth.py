"""Router de autenticación: login (OAuth2), refresh y /me."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from .. import models
from ..security import (
    crear_access_token,
    crear_refresh_token,
    get_current_user,
    get_db,
    verify_password,
)
from ..schemas import RefreshRequest, Token, UsuarioOut

router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post("/login", response_model=Token, summary="Iniciar sesión y obtener JWT")
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Autentica con username/password y devuelve access + refresh token."""
    usuario = db.execute(
        select(models.Usuario).where(models.Usuario.username == form.username)
    ).scalars().first()

    if not usuario or not verify_password(form.password, usuario.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if usuario.estado.value != "activo":
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    usuario.ultimo_acceso = func.now()
    db.commit()

    return Token(
        access_token=crear_access_token(usuario),
        refresh_token=crear_refresh_token(usuario),
    )


@router.post("/refresh", response_model=Token, summary="Renovar access token")
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    """Intercambia un refresh token válido por un nuevo par de tokens."""
    from jose import JWTError, jwt

    from ..config import settings

    try:
        data = jwt.decode(
            payload.refresh_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        if data.get("typ") != "refresh":
            raise JWTError()
        usuario = db.get(models.Usuario, int(data.get("sub")))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Refresh token inválido")

    if not usuario or usuario.estado.value != "activo":
        raise HTTPException(status_code=401, detail="Usuario inválido")

    return Token(
        access_token=crear_access_token(usuario),
        refresh_token=crear_refresh_token(usuario),
    )


@router.get("/me", response_model=UsuarioOut, summary="Usuario autenticado actual")
def me(
    usuario: models.Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Devuelve el perfil del usuario autenticado (incluye su rol)."""
    cargado = db.execute(
        select(models.Usuario)
        .options(joinedload(models.Usuario.rol))
        .where(models.Usuario.id == usuario.id)
    ).scalars().unique().one()
    return cargado
