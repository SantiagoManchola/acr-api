"""Script de inicialización: crea roles base y usuario admin.

Uso (desde la carpeta `api/` con la BD ya creada):
    python seed.py

Lee credenciales de entorno: ADMIN_USERNAME, ADMIN_PASSWORD, DATABASE_URL.
"""
from app.config import settings
from app.db import SessionLocal
from app.models import EstadoRegistro, Rol, Ubicacion, Usuario
from app.security import hash_password

ROLES_BASE = ["admin", "administrativo", "operario", "fontanero"]
UBICACIONES_BASE = ["Oficina", "Planta de tratamiento"]


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
