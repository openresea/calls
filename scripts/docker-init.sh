#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username postgres --dbname calls -f /schemas/schema.sql
psql -v ON_ERROR_STOP=1 --username postgres --dbname calls \
  -v admin_password="$ADMIN_DB_PASSWORD" -v caller_password="$CALLER_DB_PASSWORD" -v admin_email="$ADMIN_EMAIL" <<'SQL'
ALTER ROLE calls_admin PASSWORD :'admin_password';
ALTER ROLE calls_caller PASSWORD :'caller_password';
INSERT INTO pb_admin_users(email) VALUES (lower(:'admin_email'));
SQL
