#!/bin/bash
set -e

# Handle PUID/PGID for Unraid compatibility
PUID=${PUID:-1000}
PGID=${PGID:-1000}

echo "Starting Great Eye with UID=$PUID GID=$PGID"

# Update greateye user/group IDs
groupmod -o -g "$PGID" greateye
usermod -o -u "$PUID" greateye

# Fix ownership of config directory
chown -R greateye:greateye /config

# Start the application as the greateye user
# Migrations run automatically during app lifespan startup
exec su greateye -c "cd /app && exec $*"
