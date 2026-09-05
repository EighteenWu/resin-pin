#!/bin/bash
set -euo pipefail

KEY=/root/.ssh/resin-pin-deploy
REPO=/root/resin-pin

mkdir -p /root/.ssh
touch /root/.ssh/known_hosts
ssh-keyscan -t ed25519,rsa github.com >> /root/.ssh/known_hosts 2>/dev/null
ssh-keyscan -t ed25519,rsa -p 443 ssh.github.com >> /root/.ssh/known_hosts 2>/dev/null

export GIT_SSH_COMMAND="ssh -i ${KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

if [ ! -d "${REPO}/.git" ]; then
  git clone git@github.com:EighteenWu/resin-pin.git "${REPO}" \
    || GIT_SSH_COMMAND="ssh -i ${KEY} -p 443 -o IdentitiesOnly=yes" git clone git@ssh.github.com:EighteenWu/resin-pin.git "${REPO}"
else
  git -C "${REPO}" fetch origin
  git -C "${REPO}" checkout main
  git -C "${REPO}" pull --ff-only origin main
fi

umask 077
ADMIN=$(docker exec resin printenv RESIN_ADMIN_TOKEN)
PROXY=$(docker exec resin printenv RESIN_PROXY_TOKEN)
python3 - <<PY
from pathlib import Path
path = Path("${REPO}/.env")
existing = {}
if path.exists():
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        existing[key.strip()] = value
existing.update({
    "RESIN_URL": "http://resin:2260",
    "RESIN_ADMIN_TOKEN": """${ADMIN}""",
    "RESIN_PROXY_TOKEN": """${PROXY}""",
    "RESIN_PUBLIC_HOST": "pin.example.com",
    "RESIN_PUBLIC_PORT": "2260",
    "PIN_LISTEN": "0.0.0.0:2270",
    "PIN_STATE_PATH": "/data/state.json",
    "PIN_SYNC_ON_START": "true",
    "PIN_SYNC_INTERVAL_SECONDS": "86400",
    "PIN_REGIONS": "tw,jp,hk,sg,kr",
})
lines = [f"{key}={value}" for key, value in existing.items()]
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("env keys", len(existing))
PY
chmod 600 "${REPO}/.env"
unset ADMIN PROXY

mkdir -p "${REPO}/data"
cd "${REPO}"
docker compose up -d --build

ufw allow 2270/tcp comment 'resin-pin ui' >/dev/null

echo "deployed"
docker ps --filter name=resin-pin --format '{{.Names}} {{.Status}} {{.Ports}}'
