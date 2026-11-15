import hashlib

import bcrypt


def get_password_hash(password: str) -> str:
    # Используем SHA-256, чтобы обойти лимит bcrypt в 72 байта без обрезания пароля
    sha256_pwd = hashlib.sha256(password.encode("utf-8")).hexdigest()
    pwd_bytes = sha256_pwd.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return "bcrypt-sha256:" + hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    sha256_pwd = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
    pwd_bytes = sha256_pwd.encode("utf-8")
    try:
        if hashed_password.startswith("bcrypt-sha256:"):
            return bcrypt.checkpw(
                pwd_bytes, hashed_password.removeprefix("bcrypt-sha256:").encode()
            )
        # Read both unversioned formats written by earlier releases.
        if bcrypt.checkpw(pwd_bytes, hashed_password.encode()):
            return True
        raw = plain_password.encode("utf-8")
        return len(raw) <= 72 and bcrypt.checkpw(raw, hashed_password.encode())
    except (ValueError, TypeError):
        return False
