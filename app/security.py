from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.context import CryptContext

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeTimedSerializer(settings.secret_key, salt="mediawarden-session")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def sign_session(user_id: int) -> str:
    return serializer.dumps({"user_id": user_id})


def unsign_session(token: str, max_age_seconds: int = 60 * 60 * 24 * 7) -> int | None:
    try:
        data = serializer.loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    return int(data.get("user_id"))
