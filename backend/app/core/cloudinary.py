import cloudinary
import cloudinary.uploader
from .config import settings

cloudinary.config(
    cloud_name=settings.cloudinary_cloud_name,
    api_key=settings.cloudinary_api_key,
    api_secret=settings.cloudinary_api_secret,
    secure=True,
)

def _ensure_configured():
    if not (settings.cloudinary_cloud_name and settings.cloudinary_api_key and settings.cloudinary_api_secret):
        raise RuntimeError("Cloudinary is not configured. Set CLOUDINARY_URL or CLOUDINARY_CLOUD_NAME/API_KEY/API_SECRET.")

def upload_bytes(data: bytes, public_id: str, folder: str, resource_type: str = "auto"):
    _ensure_configured()
    return cloudinary.uploader.upload(
        data,
        public_id=public_id,
        folder=folder,
        resource_type=resource_type,
        overwrite=True,
        invalidate=True,
    )

def delete_asset(public_id: str, resource_type: str = "image"):
    _ensure_configured()
    return cloudinary.uploader.destroy(
        public_id,
        resource_type=resource_type,
        invalidate=True,
    )
