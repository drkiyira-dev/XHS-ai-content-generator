#!/usr/bin/env bash

# This file is sourced by the official MySQL entrypoint only while initializing
# an empty data volume. It deliberately creates the application account itself
# so that the account never temporarily receives the image's default ALL grant.

if [[ "${MYSQL_DATABASE:-}" != "xhs_ai" ]]; then
    echo >&2 "Application database initialization refused."
    exit 1
fi

app_password="$(< /run/secrets/MYSQL_APP_PASSWORD)"
if [[ ! "$app_password" =~ ^[A-Za-z0-9_-]{32,}$ ]]; then
    app_password=""
    echo >&2 "Application database credential is invalid."
    exit 1
fi

if ! docker_process_sql --database=xhs_ai >/dev/null 2>&1 <<SQL
CREATE USER 'xhs_app'@'%' IDENTIFIED BY '${app_password}';
GRANT SELECT, INSERT, UPDATE ON xhs_ai.* TO 'xhs_app'@'%';
SQL
then
    app_password=""
    echo >&2 "Application database account initialization failed."
    exit 1
fi

app_password=""
