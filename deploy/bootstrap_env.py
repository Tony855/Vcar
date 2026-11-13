#!/usr/bin/env python3
"""Create separate runtime and migration PostgreSQL credentials without rotating them."""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import secrets
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

parser = argparse.ArgumentParser()
parser.add_argument("--env-file", default=".env")
parser.add_argument("--print-db-password", action="store_true", help="Print the least-privileged runtime role password")
parser.add_argument("--print-migration-db-password", action="store_true")
parser.add_argument("--write-role-sql")
args = parser.parse_args()
path = Path(args.env_file)
values: dict[str, str] = {}
lines = path.read_text().splitlines() if path.exists() else []
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key, value = stripped.split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")

def parse_url(value: str, expected_user: str) -> tuple[str, str]:
    parts = urlsplit(value)
    if parts.scheme not in ("postgres", "postgresql"):
        raise SystemExit("Database URLs must use PostgreSQL")
    if parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("This installer only provisions local PostgreSQL; configure remote DB separately")
    if unquote(parts.username or "") != expected_user or (parts.path or "").strip("/") != "vcar":
        raise SystemExit(f"Database URL must target local role {expected_user} and database vcar")
    password = unquote(parts.password or "")
    if not password:
        raise SystemExit(f"Database URL for {expected_user} must include a password")
    return password, parts.hostname or "127.0.0.1"

runtime_url = values.get("DATABASE_URL", "")
migration_url = values.get("MIGRATION_DATABASE_URL", "")

# Existing installations used the vcar owner role as DATABASE_URL. Preserve it
# as the migration credential and create a separate runtime login.
if runtime_url:
    runtime_parts = urlsplit(runtime_url)
    runtime_user = unquote(runtime_parts.username or "")
    if runtime_user == "vcar" and not migration_url:
        migration_url = runtime_url
        runtime_url = ""

if not migration_url:
    migration_password = secrets.token_hex(32)
    migration_url = f"postgresql://vcar:{quote(migration_password, safe='')}@127.0.0.1:5432/vcar"
else:
    migration_password, _ = parse_url(migration_url, "vcar")

if not runtime_url:
    runtime_password = secrets.token_hex(32)
    runtime_url = f"postgresql://vcar_app:{quote(runtime_password, safe='')}@127.0.0.1:5432/vcar"
else:
    runtime_password, _ = parse_url(runtime_url, "vcar_app")

migration_password, _ = parse_url(migration_url, "vcar")
runtime_password, _ = parse_url(runtime_url, "vcar_app")

secret = values.get("SECRET_KEY", "")
if len(secret.encode()) < 32 or secret.startswith("REPLACE_"):
    secret = secrets.token_hex(32)

# Preserve the legacy SECRET_KEY-derived Fernet key on first upgrade so Bot
# tokens encrypted by earlier releases remain readable. Thereafter this key
# is independent and can survive routine JWT/session SECRET_KEY rotation.
encryption_key = values.get("BOT_CREDENTIALS_ENCRYPTION_KEY", "").strip()
if not encryption_key:
    encryption_key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()).decode()
try:
    if len(base64.urlsafe_b64decode(encryption_key.encode())) != 32:
        raise ValueError
except (ValueError, TypeError):
    raise SystemExit("BOT_CREDENTIALS_ENCRYPTION_KEY must be a valid Fernet key")

updates = {
    "DATABASE_URL": runtime_url,
    "MIGRATION_DATABASE_URL": migration_url,
    "SECRET_KEY": secret,
    "BOT_CREDENTIALS_ENCRYPTION_KEY": encryption_key,
}
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out).rstrip() + "\n")
os.chmod(path, 0o600)

if args.write_role_sql:
    sql = f"""DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='vcar') THEN
        CREATE ROLE vcar LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='vcar_app') THEN
        CREATE ROLE vcar_app LOGIN;
    END IF;
END
$$;
ALTER ROLE vcar LOGIN PASSWORD '{migration_password.replace("'", "''")}';
ALTER ROLE vcar_app LOGIN PASSWORD '{runtime_password.replace("'", "''")}';
GRANT CONNECT ON DATABASE vcar TO vcar_app;
GRANT USAGE ON SCHEMA public TO vcar_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vcar_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO vcar_app;
ALTER DEFAULT PRIVILEGES FOR ROLE vcar IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vcar_app;
ALTER DEFAULT PRIVILEGES FOR ROLE vcar IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO vcar_app;
"""
    sql_path = Path(args.write_role_sql)
    sql_path.write_text(sql)
    os.chmod(sql_path, 0o600)

if args.print_db_password:
    print(runtime_password)
if args.print_migration_db_password:
    print(migration_password)
