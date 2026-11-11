import hashlib

import bcrypt


def get_password_hash(password: str) -> str:
    # Используем SHA-256, чтобы обойти лимит bcrypt в 72 байта без обрезания пароля
    sha256_pwd = hashlib.sha256(password.encode("utf-8")).hexdigest()
    pwd_bytes = sha256_pwd.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    sha256_pwd = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
    pwd_bytes = sha256_pwd.encode("utf-8")
    return bcrypt.checkpw(pwd_bytes, hashed_password.encode("utf-8"))
