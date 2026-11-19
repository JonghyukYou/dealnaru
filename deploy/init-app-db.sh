#!/bin/sh
# Only runs for a fresh PostgreSQL volume.
set -eu
psql -X --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 <<'SQL'
\getenv app_password DB_APP_PASSWORD
CREATE ROLE dealnaru_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
ALTER DATABASE dealnaru OWNER TO dealnaru_app;
REVOKE CONNECT ON DATABASE dealnaru FROM PUBLIC;
SQL
