"""Create fresh local secrets without printing them or overwriting an existing file."""

import base64
import os
import re
import secrets
from pathlib import Path


def main():
    target = Path(__file__).resolve().parents[1] / ".env"
    if target.exists():
        raise SystemExit(
            ".env already exists. Edit it directly; no secrets were changed."
        )
    email = input("First administrator email: ").strip().lower()
    sender = input("Verified sender email address: ").strip().lower()
    host = input("SMTP server hostname: ").strip()
    pattern = r"[a-z0-9.!+_-]+@[a-z0-9.-]+\.[a-z]{2,}"
    if not all(
        re.fullmatch(pattern, value) for value in [email, sender]
    ) or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise SystemExit("Enter valid email addresses and an SMTP hostname.")
    values = {
        "ADMIN_EMAIL": email,
        "ALLOWED_HOSTS": "localhost,127.0.0.1",
        "FERNET_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "EMAIL_CODE_SECRET": secrets.token_urlsafe(48),
        "SMTP_HOST": host,
        "SMTP_FROM": sender,
        "SMTP_PORT": "587",
        "SMTP_SECURITY": "starttls",
        "SMTP_USERNAME": "",
        "SMTP_PASSWORD": "",
    }
    for name in ["POSTGRES_PASSWORD", "ADMIN_DB_PASSWORD", "CALLER_DB_PASSWORD"]:
        values[name] = secrets.token_urlsafe(32)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    print(
        "Created .env with owner-only permissions. Add SMTP credentials, then run docker compose up --build."
    )


if __name__ == "__main__":
    main()
