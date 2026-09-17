"""Punto de entrada de la API FastAPI (ACR).

Swagger automático en `/docs`. Auth JWT, control de acceso por rol, CORS.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import auth, dashboard, evidencias, inventario, micromedidores, planta, reportes, sectores, usuarios

app = FastAPI(
    title="API ACR — Acueducto Comunitario Acuaricaurte",
    version="1.0.0",
    description=(
        "API REST segura (FastAPI + MySQL) para gestión operativa del acueducto: "
        "autenticación, inventario, micromedidores y planta de tratamiento. "
        "Swagger en /docs."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(usuarios.router)
app.include_router(evidencias.router)
app.include_router(inventario.router)
app.include_router(micromedidores.router)
app.include_router(sectores.router)
app.include_router(planta.router)
app.include_router(reportes.router)
app.include_router(dashboard.router)


@app.get("/", tags=["Health"])
def health():
    return {"estado": "ok", "servicio": "ACR API", "swagger": "/docs"}
