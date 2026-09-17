"""Router de usuarios y roles (RF-01..RF-05)."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from .. import models
from ..config import settings
from ..schemas import (
    RolCreate,
    RolOut,
    UsuarioCreate,
    UsuarioOpcionOut,
    UsuarioOut,
    UsuariosPagina,
    UsuarioUpdate,
)
from ..security import get_current_user, get_db, hash_password, require_role
from ..services.common import sellar, como_pagina, orden_validado

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


@router.get("", response_model=UsuariosPagina, summary="Listar usuarios (paginado)")
def listar_usuarios(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    orden: str | None = Query(default=None, description="Campo de orden (whitelist del servicio)"),
    dir_orden: str = Query(default="asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_role(["admin", "administrativo"])),
):
    stmt = select(models.Usuario).options(joinedload(models.Usuario.rol))
    permitidos = {
        "nombre": models.Usuario.nombre,
        "username": models.Usuario.username,
        "identificacion": models.Usuario.identificacion,
        "estado": models.Usuario.estado,
        "ultimo_acceso": models.Usuario.ultimo_acceso,
        "rol": models.Rol.nombre,
    }
    if orden == "rol":
        # 1:1 por FK: el join no multiplica filas; joinedload usa alias propio.
        stmt = stmt.join(models.Rol, models.Usuario.rol_id == models.Rol.id)
    por_defecto = lambda s: s.order_by(models.Usuario.nombre)
    ordenar_fn = orden_validado(
        orden, dir_orden, permitidos, por_defecto, desempate=models.Usuario.id.asc()
    )
    total = int(db.execute(
        select(func.count()).select_from(models.Usuario)
    ).scalar_one())
    filas = (
        db.execute(
            ordenar_fn(stmt).limit(page_size).offset((page - 1) * page_size)
        )
        .scalars()
        .unique()
        .all()
    )
    return como_pagina(filas, total, page, page_size)


@router.get(
    "/opciones",
    response_model=list[UsuarioOpcionOut],
    summary="Usuarios ligeros para selects (responsables)",
)
def opciones_usuarios(
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_role(["admin", "administrativo", "operario"])),
):
    return db.execute(
        select(models.Usuario.id, models.Usuario.nombre, models.Usuario.estado)
        .order_by(models.Usuario.nombre)
    ).all()


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
