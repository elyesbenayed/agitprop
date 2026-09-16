"""Sécurité : chiffrement des tokens, hash des mots de passe, audit, RBAC."""
import bcrypt
from cryptography.fernet import Fernet

from .config import settings
from .database import AuditLog

_fernet = Fernet(settings.fernet_key.encode()) if settings.fernet_key else None


def encrypt_token(token: str) -> str:
    """Chiffre un access token avant stockage. Jamais de token en clair en base."""
    if _fernet is None:
        raise RuntimeError("FERNET_KEY manquante dans .env — refus de stocker un token en clair.")
    return _fernet.encrypt(token.encode()).decode()


def decrypt_token(token_enc: str) -> str:
    if _fernet is None:
        raise RuntimeError("FERNET_KEY manquante dans .env.")
    return _fernet.decrypt(token_enc.encode()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def audit(db, user_email: str, action: str, detail: str = "", ip: str | None = None):
    """Trace toute action sensible dans le journal d'audit."""
    db.add(AuditLog(user_email=user_email, action=action, detail=detail, ip=ip))
    db.commit()


def can_access_account(user, account) -> bool:
    """RBAC : un correspondant ne touche que son département ; l'admin voit tout."""
    if user.role == "admin":
        return True
    return user.department_code == account.department_code
