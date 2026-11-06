#!/usr/bin/env bash
# Despliegue automático del bot a la Raspberry Pi. No pregunta nada.
# Desde WSL: bash deploy.sh
# sshpass (una vez): sudo apt update && sudo apt install -y sshpass

set -euo pipefail

# Cambia la IP aquí. El resto se pide al ejecutar el script.
IP_RASPBERRY="192.168.1.108"
PI_USER="admin"
# Rellena estas dos en tu copia. No las subas al repositorio.
PI_PASSWORD="${PI_PASSWORD:-}"
WEB_PASSWORD="${WEB_PASSWORD:-}"
MODEL_ID="cognitivecomputations/dolphin-mistral-24b-venice-edition"

REMOTE_DIR="/home/admin/bots/bot_ia"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${IP_RASPBERRY}" ]]; then
  echo "Rellena IP_RASPBERRY al inicio del script." >&2
  exit 1
fi

read -r -p "OPENROUTER_API_KEY: " OPENROUTER_API_KEY
read -r -p "TELEGRAM_TOKEN: " TELEGRAM_TOKEN
read -r -p "ALLOWED_TELEGRAM_ID: " ALLOWED_TELEGRAM_ID

if [[ -z "${OPENROUTER_API_KEY}" || -z "${TELEGRAM_TOKEN}" || -z "${ALLOWED_TELEGRAM_ID}" ]]; then
  echo "Hacen falta OPENROUTER_API_KEY, TELEGRAM_TOKEN y ALLOWED_TELEGRAM_ID." >&2
  exit 1
fi

if ! command -v sshpass >/dev/null 2>&1; then
  echo "Falta sshpass. En Debian, Ubuntu o WSL:" >&2
  echo "  sudo apt update && sudo apt install -y sshpass" >&2
  exit 1
fi

API_KEY="${OPENROUTER_API_KEY}"

grab() {
  local key="$1"
  if [[ -f "${ROOT}/.env" ]]; then
    grep -E "^${key}=" "${ROOT}/.env" | head -n 1 | cut -d= -f2- || true
  fi
}
FAL_KEY="${FAL_KEY:-$(grab FAL_KEY)}"
REPLICATE_API_TOKEN="${REPLICATE_API_TOKEN:-$(grab REPLICATE_API_TOKEN)}"
VOICE_REF_URL="${VOICE_REF_URL:-$(grab VOICE_REF_URL)}"
VOICE_REF_URL="${VOICE_REF_URL:-https://filebin.net/jzdfd280xpdmn4v1}"

umask 077
cat > "${ROOT}/.env" <<EOF
MODEL_ID=${MODEL_ID}
API_KEY=${API_KEY}
OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
ALLOWED_TELEGRAM_ID=${ALLOWED_TELEGRAM_ID}
WEB_PASSWORD=${WEB_PASSWORD}
FAL_KEY=${FAL_KEY}
REPLICATE_API_TOKEN=${REPLICATE_API_TOKEN}
VOICE_REF_URL=${VOICE_REF_URL}
EOF

export SSHPASS="${PI_PASSWORD}"
SSH_OPTS=(
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=20
)

ssh_pi() {
  sshpass -e ssh "${SSH_OPTS[@]}" "${PI_USER}@${IP_RASPBERRY}" "$@"
}

scp_pi() {
  sshpass -e scp "${SSH_OPTS[@]}" "$@"
}

echo "==> Carpeta y venv limpio en ${PI_USER}@${IP_RASPBERRY}:${REMOTE_DIR}"
ssh_pi "bash -s" <<EOF
set -euo pipefail
echo '${PI_PASSWORD}' | sudo -S systemctl stop bot-ia.service >/dev/null 2>&1 || true
mkdir -p '${REMOTE_DIR}'
rm -rf '${REMOTE_DIR}/venv'
if ! python3 -m venv '${REMOTE_DIR}/venv'; then
  echo '${PI_PASSWORD}' | sudo -S apt-get update -qq
  echo '${PI_PASSWORD}' | sudo -S apt-get install -y python3-venv python3-pip
  rm -rf '${REMOTE_DIR}/venv'
  python3 -m venv '${REMOTE_DIR}/venv'
fi
test -x '${REMOTE_DIR}/venv/bin/python'
EOF

echo "==> Copiando bot.py, requirements.txt, .env e index.html"
ssh_pi "mkdir -p '${REMOTE_DIR}/static'"
scp_pi \
  "${ROOT}/bot.py" \
  "${ROOT}/requirements.txt" \
  "${ROOT}/.env" \
  "${PI_USER}@${IP_RASPBERRY}:${REMOTE_DIR}/"
scp_pi \
  "${ROOT}/static/index.html" \
  "${ROOT}/static/script.js" \
  "${ROOT}/static/style.css" \
  "${PI_USER}@${IP_RASPBERRY}:${REMOTE_DIR}/static/"

echo "==> Instalando dependencias en el venv de la Pi"
ssh_pi "bash -s" <<EOF
set -euo pipefail
sed -i 's/\r$//' '${REMOTE_DIR}/bot.py' '${REMOTE_DIR}/requirements.txt' '${REMOTE_DIR}/.env' '${REMOTE_DIR}/static/index.html' '${REMOTE_DIR}/static/script.js' '${REMOTE_DIR}/static/style.css'
chmod 600 '${REMOTE_DIR}/.env'
'${REMOTE_DIR}/venv/bin/pip' install --upgrade pip
'${REMOTE_DIR}/venv/bin/pip' install -r '${REMOTE_DIR}/requirements.txt'
EOF

echo "==> Generando y copiando bot-ia.service"
SERVICE_TMP="$(mktemp)"
cat > "${SERVICE_TMP}" <<'UNIT'
[Unit]
Description=Bot de Telegram (interfaz LLM)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=admin
Group=admin
WorkingDirectory=/home/admin/bots/bot_ia
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/home/admin/bots/bot_ia/.env
# http://IP/ (puerto 80) llega al panel del puerto 5000
ExecStartPre=+/bin/sh -c 'iptables -t nat -C PREROUTING -p tcp --dport 80 -j REDIRECT --to-ports 5000 2>/dev/null || iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-ports 5000'
ExecStart=/home/admin/bots/bot_ia/venv/bin/python /home/admin/bots/bot_ia/bot.py
MemoryMax=150M
MemoryAccounting=true
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

scp_pi "${SERVICE_TMP}" "${PI_USER}@${IP_RASPBERRY}:${REMOTE_DIR}/bot-ia.service"
rm -f "${SERVICE_TMP}"

echo "==> Habilitando e iniciando bot-ia.service"
ssh_pi "bash -s" <<EOF
set -euo pipefail
sed -i 's/\r$//' '${REMOTE_DIR}/bot-ia.service'
echo '${PI_PASSWORD}' | sudo -S cp '${REMOTE_DIR}/bot-ia.service' /etc/systemd/system/bot-ia.service
echo '${PI_PASSWORD}' | sudo -S chmod 644 /etc/systemd/system/bot-ia.service
echo '${PI_PASSWORD}' | sudo -S systemctl daemon-reload
echo '${PI_PASSWORD}' | sudo -S systemctl enable bot-ia.service
echo '${PI_PASSWORD}' | sudo -S systemctl restart bot-ia.service
sleep 5
echo '${PI_PASSWORD}' | sudo -S systemctl status bot-ia.service --no-pager -l || true
echo '--- journal ---'
echo '${PI_PASSWORD}' | sudo -S journalctl -u bot-ia.service -n 30 --no-pager || true
state="\$(echo '${PI_PASSWORD}' | sudo -S systemctl is-active bot-ia.service || true)"
pid="\$(echo '${PI_PASSWORD}' | sudo -S systemctl show -p MainPID --value bot-ia.service || true)"
echo "ESTADO=\${state} PID=\${pid}"
if [[ "\${state}" != "active" || "\${pid}" == "0" || -z "\${pid}" ]]; then
  exit 1
fi
EOF

echo "==> Servicio bot-ia activo en ${IP_RASPBERRY}"
