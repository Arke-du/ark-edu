from cryptography.fernet import Fernet
import secrets

print("SECRET_KEY=" + secrets.token_urlsafe(48))
print("LGPD_ENCRYPTION_KEY=" + Fernet.generate_key().decode())
print("LGPD_HASH_KEY=" + secrets.token_urlsafe(64))
