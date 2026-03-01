# Unraid Deployment: Dockerfile + CA Template

## Overview
Great Eye is deployed on Unraid as a Docker container. This spec
covers the Dockerfile, the Unraid Community Applications XML template,
and deployment configuration.

## Dockerfile

```dockerfile
FROM python:3.12-slim

# Labels for Unraid CA
LABEL maintainer="greateye"
LABEL org.opencontainers.image.title="Great Eye"
LABEL org.opencontainers.image.description="Unified homelab operations dashboard"

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# Create app user
RUN groupadd -g 1000 greateye && \
    useradd -u 1000 -g greateye -m greateye

# App directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY static/ ./static/
COPY templates/ ./templates/
COPY alembic/ ./alembic/
COPY alembic.ini .

# Default data directory
RUN mkdir -p /config && chown greateye:greateye /config

# Runtime config
ENV DATABASE_URL=sqlite:///config/greateye.db
ENV PYTHONUNBUFFERED=1

# PUID/PGID support (handled by entrypoint)
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8484

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8484"]
```

## Entrypoint Script

```bash
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

# Run database migrations
su greateye -c "cd /app && python -m alembic upgrade head"

# Start the application as the greateye user
exec su greateye -c "cd /app && exec $*"
```

## Unraid CA XML Template

```xml
<?xml version="1.0"?>
<Container version="2">
  <Name>GreatEye</Name>
  <Repository>ghcr.io/greateye/greateye:latest</Repository>
  <Registry>https://github.com/greateye/greateye</Registry>
  <Branch>
    <Tag>latest</Tag>
    <TagDescription>Latest stable release</TagDescription>
  </Branch>
  <Branch>
    <Tag>develop</Tag>
    <TagDescription>Development builds (may be unstable)</TagDescription>
  </Branch>
  <Network>bridge</Network>
  <Privileged>false</Privileged>
  <Support>https://github.com/greateye/greateye/issues</Support>
  <Project>https://github.com/greateye/greateye</Project>
  <Overview>
    Great Eye is a unified homelab operations dashboard. Monitor and manage
    your Sonarr, Radarr, Prowlarr, qBittorrent, Plex, Tautulli, Pi-hole,
    Unbound, Seerr, and Docker containers from a single dark-themed interface.
    Features include real-time updates via SSE, role-based access control with
    Plex OAuth, and provider health monitoring.
  </Overview>
  <Category>Tools: Productivity:</Category>
  <WebUI>http://[IP]:[PORT:8484]/</WebUI>
  <Icon>https://raw.githubusercontent.com/greateye/greateye/main/static/icons/greateye.png</Icon>
  <ExtraParams/>
  <DateInstalled/>
  <Description>
    Unified homelab operations dashboard for monitoring and managing
    media automation, DNS, and container infrastructure.
  </Description>

  <!-- Port Mapping -->
  <Config
    Name="Web UI Port"
    Target="8484"
    Default="8484"
    Mode="tcp"
    Description="Great Eye web interface port"
    Type="Port"
    Display="always"
    Required="true"
    Mask="false">8484</Config>

  <!-- Persistent Data -->
  <Config
    Name="Config Path"
    Target="/config"
    Default="/mnt/user/appdata/greateye"
    Mode="rw"
    Description="Persistent configuration and database storage"
    Type="Path"
    Display="always"
    Required="true"
    Mask="false">/mnt/user/appdata/greateye</Config>

  <!-- Docker Socket (Optional) -->
  <Config
    Name="Docker Socket"
    Target="/var/run/docker.sock"
    Default="/var/run/docker.sock"
    Mode="ro"
    Description="Docker socket for container monitoring (read-only). Set to rw for container restart capability."
    Type="Path"
    Display="always"
    Required="false"
    Mask="false">/var/run/docker.sock</Config>

  <!-- Required Environment Variables -->
  <Config
    Name="SECRET_KEY"
    Target="SECRET_KEY"
    Default=""
    Description="Encryption key for sensitive data. REQUIRED. Generate with: python -c 'import secrets; print(secrets.token_hex(32))'. If changed, all encrypted provider configs are lost."
    Type="Variable"
    Display="always"
    Required="true"
    Mask="true"/>

  <!-- Plex OAuth (Optional) -->
  <Config
    Name="PLEX_CLIENT_ID"
    Target="PLEX_CLIENT_ID"
    Default=""
    Description="Plex application client ID for OAuth. Required if using Plex sign-in. Register at https://www.plex.tv/claim/"
    Type="Variable"
    Display="always"
    Required="false"
    Mask="false"/>

  <!-- User/Group ID -->
  <Config
    Name="PUID"
    Target="PUID"
    Default="99"
    Description="User ID for file permissions (99 = nobody on Unraid)"
    Type="Variable"
    Display="advanced"
    Required="false"
    Mask="false">99</Config>

  <Config
    Name="PGID"
    Target="PGID"
    Default="100"
    Description="Group ID for file permissions (100 = users on Unraid)"
    Type="Variable"
    Display="advanced"
    Required="false"
    Mask="false">100</Config>

  <!-- Optional Environment Variables -->
  <Config
    Name="TZ"
    Target="TZ"
    Default="America/New_York"
    Description="Timezone for the container"
    Type="Variable"
    Display="advanced"
    Required="false"
    Mask="false">America/New_York</Config>

  <Config
    Name="LOG_LEVEL"
    Target="LOG_LEVEL"
    Default="INFO"
    Description="Logging level (DEBUG, INFO, WARNING, ERROR)"
    Type="Variable"
    Display="advanced"
    Required="false"
    Mask="false">INFO</Config>

</Container>
```

## Configuration Files in /config

The `/config` mount persists across container updates:

```
/config/
  greateye.db              # SQLite database (auto-created)
  greateye.db-wal          # SQLite WAL file
  greateye.db-shm          # SQLite shared memory
```

No config files need manual editing. All configuration is done
through the web UI after the first-time setup wizard.

## Environment Variables Reference

| Variable        | Required | Default            | Description                          |
|-----------------|----------|--------------------|--------------------------------------|
| SECRET_KEY      | Yes      | —                  | Fernet encryption key derivation     |
| PLEX_CLIENT_ID  | No       | —                  | Plex OAuth app ID                    |
| DATABASE_URL    | No       | sqlite:///config/greateye.db | Database path        |
| PUID            | No       | 1000               | Container user ID                    |
| PGID            | No       | 1000               | Container group ID                   |
| TZ              | No       | UTC                | Timezone                             |
| LOG_LEVEL       | No       | INFO               | Logging verbosity                    |

## Networking Notes

### Accessing Homelab Services
The Great Eye container needs network access to all monitored services.
On Unraid with bridge networking, this means:

- Services on the same Unraid host: accessible via Unraid's IP
  (e.g., 10.0.0.45:8989 for Sonarr)
- Services on other hosts: accessible if on the same LAN
- Docker containers on custom networks: may need explicit network
  attachment or host networking mode

If bridge mode doesn't work (e.g., containers on isolated Docker
networks), switching to `host` networking mode resolves most issues
at the cost of port management.

### Docker Socket Access
The socket mount allows Great Eye to talk to the Docker daemon.
Default `:ro` mode allows listing and inspecting containers.
Change to `:rw` in the Unraid template if container restart
actions are desired.

## Backup Strategy

### What to Back Up
- `/mnt/user/appdata/greateye/` — the entire config directory
- Contains the SQLite database with all configuration, user accounts,
  provider configs, metrics history

### What's NOT in the Backup
- Provider API keys are encrypted in the database. If SECRET_KEY
  changes and you restore the database, encrypted fields are
  unrecoverable. Back up the SECRET_KEY separately.

### Unraid Integration
- Standard Unraid appdata backup covers Great Eye
- CA Backup / Restore plugin backs up /mnt/user/appdata/ by default
- Database is in WAL mode — snapshot-safe for backup

## Health Check

The Dockerfile can include a health check for Docker's built-in
monitoring:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8484/auth/login || exit 1
```

This checks that the web server is responding. The login page is
used because it doesn't require authentication.

## Build & Release

### Multi-Architecture
Build for both amd64 and arm64 to support different Unraid hardware:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t ghcr.io/greateye/greateye:latest \
  --push .
```

### Versioning
- Tags: `latest`, `v1.0.0`, `v1.0`, `v1`
- Develop branch: `develop` tag
- Semantic versioning for releases
