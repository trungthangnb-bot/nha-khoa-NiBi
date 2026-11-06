"""Password hashing, session tokens and brute-force limiter (stdlib only)."""
import hashlib
import hmac
import secrets
import threading
import time

_ITER = 200_000


def hash_secret(secret: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, _ITER)
    return f"pbkdf2${_ITER}${salt.hex()}${dk.hex()}"


def verify_secret(secret: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    """Only the digest is stored, so a leaked DB cannot be replayed as sessions."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Limiter:
    """Block a key for `lock_seconds` after `max_fails` failures inside `window` seconds."""

    def __init__(self, max_fails: int, window: int, lock_seconds: int):
        self.max_fails, self.window, self.lock = max_fails, window, lock_seconds
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        self._mu = threading.Lock()

    def remaining(self, key: str) -> int:
        with self._mu:
            until = self._locked.get(key, 0)
            left = int(until - time.time())
            if left <= 0:
                self._locked.pop(key, None)
                return 0
            return left

    def fail(self, key: str) -> None:
        now = time.time()
        with self._mu:
            hits = [t for t in self._fails.get(key, []) if now - t < self.window] + [now]
            self._fails[key] = hits
            if len(hits) >= self.max_fails:
                self._locked[key] = now + self.lock
                self._fails.pop(key, None)

    def ok(self, key: str) -> None:
        with self._mu:
            self._fails.pop(key, None)
