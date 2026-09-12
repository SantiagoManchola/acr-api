"""Router de usuarios y roles (RF-01..RF-05)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .. import models
from ..config import settings
from ..schemas import (
    RolCreate,
    RolOut,
    UsuarioCreate,
    UsuarioOut,
    UsuarioUpdate,
)
from ..security import get_current_user, get_db, hash_password, require_role
from ..services.common import sellar

router = APIRouter(prefix="/usuarios", tags=["Usuarios y roles"])


# ----------------------------- Roles -----------------------------------------
@router.get("/roles", response_model=list[RolOut], summary="Listar roles")
def listar_roles(
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_role(["admin", "administrativo"])),
):
    return db.execute(select(models.Rol)).scalars().all()


@router.post(
    "/roles",
    response_model=RolOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear rol",
)
def crear_rol(
    payload: RolCreate,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_role(["admin"])),
):
    if db.execute(select(models.Rol).where(models.Rol.nombre == payload.nombre)).first():
        raise HTTPException(400, "El rol ya existe")
    rol = models.Rol(nombre=payload.nombre, descripcion=payload.descripcion)
    db.add(rol)
    db.commit()
    db.refresh(rol)
    return rol


# ----------------------------- Usuarios --------------------------------------
def _cargar(db, usuario_id):
    return db.execute(
        select(models.Usuario)
        .options(joinedload(models.Usuario.rol))
        .where(models.Usuario.id == usuario_id)
    ).scalars().unique().one_or_none()


@router.get("", response_model=list[UsuarioOut], summary="Listar usuarios")
def listar_usuarios(
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_role(["admin", "administrativo"])),
):
    return (
        db.execute(
            select(models.Usuario).options(joinedload(models.Usuario.rol))
        )
        .scalars()
        .unique()
        .all()
    )


@router.post(
    "",
    response_model=UsuarioOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear usuario",
)
def crear_usuario(
    payload: UsuarioCreate,
    db: Session = Depends(get_db),
    admin: models.Usuario = Depends(require_role(["admin"])),
):
    if db.execute(
        select(models.Usuario).where(models.Usuario.username == payload.username)
    ).first():
        raise HTTPException(400, "El username ya está en uso")
    rol = db.get(models.Rol, payload.rol_id)
    if not rol:
        raise HTTPException(400, "rol_id inválido")

    usuario = models.Usuario(
        nombre=payload.nombre,
        identificacion=payload.identificacion,
        username=payload.username,
        password_hash=hash_password(payload.password),
        rol_id=payload.rol_id,
        estado=models.EstadoRegistro.activo,
    )
    sellar(usuario, admin, nuevo=True)
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return _cargar(db, usuario.id)


@router.get("/{usuario_id}", response_model=UsuarioOut, summary="Obtener usuario")
def obtener_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_role(["admin", "administrativo"])),
):
    usuario = _cargar(db, usuario_id)
    if not usuario:
        raise HTTPException(404, "Usuario no encontrado")
    return usuario


@router.patch("/{usuario_id}", response_model=UsuarioOut, summary="Actualizar usuario")
def actualizar_usuario(
    usuario_id: int,
    payload: UsuarioUpdate,
    db: Session = Depends(get_db),
    admin: models.Usuario = Depends(require_role(["admin"])),
):
    usuario = db.get(models.Usuario, usuario_id)
    if not usuario:
        raise HTTPException(404, "Usuario no encontrado")
    datos = payload.model_dump(exclude_unset=True)
    if usuario.username == settings.admin_username and datos.get("estado") == models.EstadoRegistro.inactivo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No se puede inactivar al superadministrador",
        )
    password = datos.pop("password", None)
    if password:
        usuario.password_hash = hash_password(password)
    for k, v in datos.items():
        setattr(usuario, k, v)
    sellar(usuario, admin, nuevo=False)
    db.commit()
    return _cargar(db, usuario.id)


@router.delete("/{usuario_id}", summary="Eliminar usuario (soft delete)")
def eliminar_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
    admin: models.Usuario = Depends(require_role(["admin"])),
):
    usuario = db.get(models.Usuario, usuario_id)
    if not usuario:
        raise HTTPException(404, "Usuario no encontrado")
    if usuario.username == settings.admin_username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No se puede eliminar el superadministrador",
        )
    usuario.estado = models.EstadoRegistro.inactivo
    sellar(usuario, admin, nuevo=False)
    db.commit()
    return {"ok": True, "mensaje": "Usuario inactivado"}
