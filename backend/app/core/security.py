from datetime import datetime, timedelta, timezone
import jwt
import bcrypt
from .config import settings
import re

ALGORITHM = "HS256"


def normalize_phone(value: str) -> str:
    """Return the canonical Indian mobile form used by the users collection.

    The UI accepts 10 digits while older records may contain +91 / 91 prefixes
    or spaces/dashes. Keeping one canonical form prevents valid accounts from
    failing authentication after a UI/client change.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return digits


def phone_lookup_candidates(value: str) -> list[str]:
    canonical = normalize_phone(value)
    if not canonical:
        return []
    return list(dict.fromkeys([
        canonical,
        f"91{canonical}",
        f"+91{canonical}",
    ]))


def phone_legacy_regex(value: str) -> str | None:
    canonical = normalize_phone(value)
    if len(canonical) != 10:
        return None
    # Accept old values such as +91 98765-43210 without weakening the
    # canonical lookup used for normal records.
    chunks = "[\\s-]*".join(re.escape(ch) for ch in canonical)
    return rf"^(?:\+?91[\s-]*)?{chunks}$"

def hash_password(value: str) -> str:
    """Create a bcrypt password hash without Passlib's deprecated bcrypt adapter.

    Passlib 1.7.4 expects ``bcrypt.__about__.__version__``, which newer bcrypt
    releases no longer expose. That produces the noisy
    ``error reading bcrypt version`` traceback and can make authentication
    unreliable. The application only uses bcrypt, so use the maintained
    bcrypt package directly while keeping compatibility with existing bcrypt
    hashes in MongoDB.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("Password must not be empty")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password is too long for bcrypt (maximum 72 bytes)")
    return bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(value: str, hashed: str) -> bool:
    """Verify existing bcrypt hashes directly; no Passlib backend probing."""
    if not isinstance(value, str) or not isinstance(hashed, str) or not hashed:
        return False
    try:
        return bool(bcrypt.checkpw(value.encode("utf-8"), hashed.encode("utf-8")))
    except (ValueError, TypeError, UnicodeError):
        return False

def create_access_token(user_id: str, role: str, tenant_id: str | None, password_changed_at=None):
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    changed_ts = None
    if password_changed_at:
        if isinstance(password_changed_at, datetime):
            changed_ts = password_changed_at.timestamp()
        else:
            try:
                changed_ts = datetime.fromisoformat(str(password_changed_at).replace("Z", "+00:00")).timestamp()
            except Exception:
                changed_ts = None
    payload = {"sub": user_id, "role": role, "tenant_id": tenant_id, "exp": exp, "pwd_changed_at": changed_ts}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)

def decode_token(token: str):
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
