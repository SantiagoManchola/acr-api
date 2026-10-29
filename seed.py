"""Script de inicialización: crea roles base y usuario admin.

Uso (desde la carpeta `api/` con la BD ya creada):
    python seed.py

Lee credenciales de entorno: ADMIN_USERNAME, ADMIN_PASSWORD, DATABASE_URL.
"""
from app.config import settings
from app.db import SessionLocal
from app.models import (
    ComponentePlanta,
    EstadoComponentePlanta,
    EstadoOperativoComponente,
    EstadoRegistro,
    Rol,
    Ubicacion,
    Usuario,
)
from app.security import hash_password
from app.services.common import ahora_colombia, hoy_colombia

ROLES_BASE = ["admin", "administrativo", "operario", "fontanero"]
UBICACIONES_BASE = ["Oficina", "Planta de tratamiento"]

# Partes iniciales del diagrama lineal de la planta. El admin puede
# renombrarlas, reordenarlas o agregar más desde el CMS.
COMPONENTES_BASE = [
    "Fuente de abastecimiento quebrada",
    "Bocatoma",
    "Red de aducción",
    "Desarenador",
    "Sistema de coagulación",
    "Tanque de floculación",
    "Sedimentador",
    "Red de conducción",
    "Sistema de desinfección",
    "Tanques de almacenamiento",
]


def get_or_create(db, model, defaults=None, **filters):
    """Devuelve (instancia, creado). Crea solo si no existe ningún registro
    que cumpla los `filters`; si ya existe, retorna el existente sin tocarlo."""
    instancia = db.query(model).filter_by(**filters).first()
    if instancia is not None:
        return instancia, False
    params = {**filters, **(defaults or {})}
    instancia = model(**params)
    db.add(instancia)
    db.flush()
    return instancia, True


def main() -> None:
    db = SessionLocal()
    try:
        creados = []

        # Roles base
        for nombre in ROLES_BASE:
            _, nuevo = get_or_create(
                db, Rol, defaults={"descripcion": f"Rol {nombre}"}, nombre=nombre
            )
            if nuevo:
                creados.append(f"rol '{nombre}'")

        # Ubicaciones base del sistema (oficina, planta de tratamiento, ...)
        for nombre in UBICACIONES_BASE:
            _, nueva = get_or_create(
                db, Ubicacion, defaults={"descripcion": f"Ubicación {nombre}"}, nombre=nombre
            )
            if nueva:
                creados.append(f"ubicación '{nombre}'")

        rol_admin = db.query(Rol).filter(Rol.nombre == "admin").first()

        # Usuario admin
        admin, nuevo = get_or_create(
            db,
            Usuario,
            defaults={
                "nombre": "Administrador ACR",
                "password_hash": hash_password(settings.admin_password),
                "rol_id": rol_admin.id,
                "estado": EstadoRegistro.activo,
            },
            username=settings.admin_username,
        )
        if nuevo:
            creados.append(f"usuario admin '{settings.admin_username}'")

        # Partes base del diagrama de la planta + su estado inicial.
        # Solo se siembran si el catálogo está vacío: si el admin ya renombró
        # o reordenó partes, volver a correr el seed no debe resucitar nombres
        # anteriores. "En funcionamiento" es el estado asumido al arrancar.
        if db.query(ComponentePlanta).count() == 0:
            for posicion, nombre in enumerate(COMPONENTES_BASE, start=1):
                db.add(ComponentePlanta(
                    nombre=nombre,
                    orden=posicion,
                    estado=EstadoRegistro.activo,
                    created_by=admin.id,
                    updated_by=admin.id,
                ))
            db.flush()
            creados.append(f"{len(COMPONENTES_BASE)} partes de planta")

        estados_iniciales = 0
        componentes_activos = db.query(ComponentePlanta).filter(
            ComponentePlanta.estado == EstadoRegistro.activo
        ).all()
        for componente in componentes_activos:
            tiene_estado = (
                db.query(EstadoComponentePlanta)
                .filter(EstadoComponentePlanta.componente_id == componente.id)
                .first()
            )
            if not tiene_estado:
                db.add(EstadoComponentePlanta(
                    componente_id=componente.id,
                    estado=EstadoOperativoComponente.funcionando,
                    nota="Estado inicial",
                    fecha=hoy_colombia(),
                    hora=ahora_colombia().time().replace(microsecond=0),
                    responsable_id=admin.id,
                    created_by=admin.id,
                    updated_by=admin.id,
                ))
                estados_iniciales += 1
        if estados_iniciales:
            creados.append(f"{estados_iniciales} estado(s) inicial(es) de planta")

        # Los estados iniciales creados por la migración 010 no tienen
        # responsable (la tabla de usuarios podía estar vacía). Al correr el
        # seed con el admin ya creado, se les asigna para que el historial
        # muestre quién hizo el arranque.
        for evento in db.query(EstadoComponentePlanta).filter(
            EstadoComponentePlanta.responsable_id.is_(None),
            EstadoComponentePlanta.created_by.is_(None),
            EstadoComponentePlanta.nota == "Estado inicial",
        ).all():
            evento.responsable_id = admin.id
            evento.created_by = evento.created_by or admin.id
            evento.updated_by = evento.updated_by or admin.id

        db.commit()

        if creados:
            print("Creado(s):", ", ".join(creados))
        else:
            print("Inicialización omitida: todo lo base ya existe.")
        print("Roles base:", ", ".join(ROLES_BASE))
    finally:
        db.close()


if __name__ == "__main__":
    main()
