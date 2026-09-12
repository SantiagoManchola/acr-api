"""Configuración del API ACR (pydantic-settings).

Lee variables de entorno desde `.env` (ubicado en la carpeta `api/`).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://acr_user:acr_password@localhost:3306/acr"
    jwt_secret: str = "cambia-este-secreto-en-produccion"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    refresh_token_expire_days: int = 7
    cors_origins: str = "*"
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # Cloudflare R2 — evidencias fotográficas (bucket PÚBLICO de lectura).
    # Sin estas variables el módulo de fotos queda deshabilitado (501 claro).
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    # Base pública de lectura, ej. https://pub-xxx.r2.dev o https://fotos.tudominio.com
    r2_public_base_url: str = ""

    @property
    def r2_enabled(self) -> bool:
        return bool(
            self.r2_account_id
            and self.r2_access_key_id
            and self.r2_secret_access_key
            and self.r2_bucket
            and self.r2_public_base_url
        )

    @property
    def r2_endpoint_url(self) -> str:
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


settings = Settings()
