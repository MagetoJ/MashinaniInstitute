#!/usr/bin/env bash
# Backs up the production database and filestore into ./backups (keeps 14 days). Run from cron.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env.prod; set +a
DC="docker compose --env-file .env.prod -f docker-compose.prod.yml"
STAMP=$(date +%F_%H%M)
mkdir -p backups
$DC exec -T db pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "backups/db_${STAMP}.dump"
$DC exec -T web tar -C /var/lib/odoo -czf - filestore > "backups/filestore_${STAMP}.tar.gz"
find backups -type f -mtime +14 -delete
echo "Backup written: backups/db_${STAMP}.dump, backups/filestore_${STAMP}.tar.gz"
