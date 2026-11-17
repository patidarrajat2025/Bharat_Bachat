from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Required at runtime. Local values come from backend/.env;
    # Render production values come from Render Environment Variables.
    mongodb_uri: str = ""
    mongodb_db: str = "bharat_bachat"

    jwt_secret: str = ""
    jwt_expire_minutes: int = 1440

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    seed_superadmin_phone: str = ""
    seed_superadmin_password: str = ""

    upload_dir: str = "./uploads"
    max_upload_mb: int = 5

    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    cloudinary_url: str = ""


    model_config = SettingsConfigDict(
        env_file=(Path(__file__).resolve().parents[2] / ".env",),
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()

# Backward-compatible aliases for older local .env files.
import os
try:
    from dotenv import dotenv_values
    _legacy_env = {}
    _env_path = Path(__file__).resolve().parents[2] / ".env"
    if _env_path.exists():
        _legacy_env.update({k: v for k, v in dotenv_values(_env_path).items() if v is not None})
except Exception:
    _legacy_env = {}
if not settings.mongodb_uri: settings.mongodb_uri = os.getenv("MONGO_URL", "") or _legacy_env.get("MONGO_URL", "") or _legacy_env.get("MONGODB_URI", "")
if settings.mongodb_db == "bharat_bachat": settings.mongodb_db = os.getenv("DB_NAME", "") or _legacy_env.get("DB_NAME", "") or settings.mongodb_db
if not settings.jwt_secret: settings.jwt_secret = os.getenv("JWT_SECRET", "") or _legacy_env.get("JWT_SECRET", "")
# Prefer a complete CLOUDINARY_URL when supplied. This prevents a stale
# CLOUDINARY_CLOUD_NAME value from overriding the authoritative URL.
raw = settings.cloudinary_url or os.getenv("CLOUDINARY_URL", "") or _legacy_env.get("CLOUDINARY_URL", "")
if raw.startswith("cloudinary://"):
    from urllib.parse import urlparse
    u = urlparse(raw)
    settings.cloudinary_api_key = u.username or settings.cloudinary_api_key
    settings.cloudinary_api_secret = u.password or settings.cloudinary_api_secret
    settings.cloudinary_cloud_name = u.hostname or settings.cloudinary_cloud_name

if not settings.mongodb_uri or not settings.jwt_secret: raise RuntimeError("MONGODB_URI/MONGO_URL and JWT_SECRET are required")
