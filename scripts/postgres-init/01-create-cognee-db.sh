#!/bin/bash
set -e

# Create a separate database for Cognee on first Postgres boot.
# Runs only when pgdata volume is empty (standard docker-entrypoint-initdb.d behavior).

: "${COGNEE_DB_NAME:=cognee_db}"

echo "Creating Cognee database: ${COGNEE_DB_NAME}"

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<-EOSQL
    SELECT 'CREATE DATABASE ${COGNEE_DB_NAME}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${COGNEE_DB_NAME}')\gexec
    GRANT ALL PRIVILEGES ON DATABASE ${COGNEE_DB_NAME} TO ${POSTGRES_USER};
EOSQL
