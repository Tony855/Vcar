#!/usr/bin/env python3
"""Create safe first-install environment defaults without rotating existing credentials."""
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

parser = argparse.ArgumentParser()
parser.add_argument("--env-file", default=".env")
parser.add_argument("--print-db-password", action="store_true")
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

database_url = values.get("DATABASE_URL", "")
if not database_url:
    password = secrets.token_hex(32)
    database_url = f"postgresql://vcar:{quote(password, safe='')}@127.0.0.1:5432/vcar"
    values["DATABASE_URL"] = database_url
parts = urlsplit(database_url)
if parts.scheme not in ("postgres", "postgresql"):
    raise SystemExit("DATABASE_URL must use PostgreSQL")
if parts.hostname not in ("127.0.0.1", "localhost", "::1"):
    raise SystemExit("This installer only provisions a local PostgreSQL database; configure remote DB separately")
if unquote(parts.username or "") != "vcar" or (parts.path or "").strip("/") != "vcar":
    raise SystemExit("This installer currently provisions the local vcar database/user; use a matching DATABASE_URL")
password = unquote(parts.password or "")
if not password:
    raise SystemExit("DATABASE_URL must include a non-empty password")
secret = values.get("SECRET_KEY", "")
if len(secret.encode()) < 32 or secret.startswith("REPLACE_"):
    values["SECRET_KEY"] = secrets.token_hex(32)

# Keep all user-provided settings and comments; replace only generated values.
updates = {"DATABASE_URL": values["DATABASE_URL"], "SECRET_KEY": values["SECRET_KEY"]}
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
    sql_path = Path(args.write_role_sql)
    sql_path.write_text("ALTER ROLE vcar LOGIN PASSWORD '" + password.replace("'", "''") + "';" + chr(10))
    os.chmod(sql_path, 0o600)
if args.print_db_password:
    print(password)
