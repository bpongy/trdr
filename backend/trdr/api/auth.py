"""Authentification : mot de passe + code Google Authenticator (TOTP), puis jeton JWT."""

import hashlib
import hmac
import secrets
import threading
from datetime import datetime, timedelta

import jwt
import pyotp

from ..config import Settings
from ..db import utcnow

MAX_FAILURES = 5
LOCK_DURATION = timedelta(minutes=15)
TOTP_STEP = 30


def hash_password(password: str, *, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    """Empreinte scrypt, sans caractère `$` pour tenir sans échappement dans un .env."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt:{n}:{r}:{p}:{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split(":")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected)


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class AuthService:
    """Un seul utilisateur. L'état (verrouillage, dernier code utilisé) est en mémoire :
    l'API doit donc tourner avec un seul worker."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._lock = threading.Lock()
        self._failures = 0
        self._locked_until: datetime | None = None
        self._last_counter = -1

    def _totp_counter(self, code: str, now: datetime) -> int | None:
        """Pas de temps du code s'il est valide (tolérance : un pas avant et après)."""
        totp = pyotp.TOTP(self._settings.auth_totp_secret)
        current = int(now.timestamp()) // TOTP_STEP
        for counter in (current - 1, current, current + 1):
            if hmac.compare_digest(totp.at(counter * TOTP_STEP), code):
                return counter
        return None

    def login(self, password: str, code: str, now: datetime | None = None) -> str:
        now = now or utcnow()
        if not self._settings.auth_configured:
            raise AuthError(503, "Authentification non configurée")
        with self._lock:
            if self._locked_until and now < self._locked_until:
                raise AuthError(429, "Trop de tentatives, réessayez plus tard")

            password_ok = verify_password(password, self._settings.auth_password_hash)
            counter = self._totp_counter(code.replace(" ", ""), now)
            # Un code déjà utilisé est refusé, même s'il est encore dans sa fenêtre de validité.
            if not (password_ok and counter is not None and counter > self._last_counter):
                self._failures += 1
                if self._failures >= MAX_FAILURES:
                    self._failures = 0
                    self._locked_until = now + LOCK_DURATION
                raise AuthError(401, "Identifiants invalides")

            self._failures = 0
            self._locked_until = None
            self._last_counter = counter

        expires = now + timedelta(minutes=self._settings.jwt_ttl_minutes)
        return jwt.encode({"sub": "owner", "iat": now, "exp": expires}, self._settings.jwt_secret, algorithm="HS256")

    def verify_token(self, token: str) -> bool:
        if not self._settings.auth_configured:
            return False
        try:
            jwt.decode(token, self._settings.jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            return False
        return True
