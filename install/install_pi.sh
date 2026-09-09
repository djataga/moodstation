#!/usr/bin/env bash
# Installation MoodStation sur Raspberry Pi OS (Lite 64 bits recommandé).
# Usage : sudo bash install/install_pi.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> MoodStation — installation sur $(uname -m)"

if [[ "$(uname -m)" != "aarch64" && "$(uname -m)" != "armv7l" ]]; then
    echo "!! Ce script cible un Raspberry Pi (aarch64/armv7l). Continuons quand même…"
fi

echo "==> Paquets système"
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip git cifs-utils nfs-common zram-tools

echo "==> Environnement virtuel Python"
cd "${APP_DIR}"
if [[ ! -d .venv ]]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -e . -q

echo "==> Configuration"
if [[ ! -f config.yaml ]]; then
    cp config.example.yaml config.yaml
    echo "   config.yaml créé depuis l'exemple — éditez-le (host, compte, mot de passe) !"
else
    echo "   config.yaml existant conservé."
fi

echo "==> Liens commande + service systemd"
ln -sf "${APP_DIR}/.venv/bin/moodstation" /usr/local/bin/moodstation
if [[ -d /etc/systemd/system ]]; then
    cp systemd/moodstation-sync.service /etc/systemd/system/
    cp systemd/moodstation-sync.timer /etc/systemd/system/
    systemctl daemon-reload || true
    echo "   Timer de sync nocturne installé (désactivé par défaut)."
    echo "   Activez-le après avoir édité config.yaml :"
    echo "     sudo systemctl enable --now moodstation-sync.timer"
fi

cat <<'FIN'

Installation terminée. Prochaines étapes :

  1) nano config.yaml            # host NAS, compte, mot de passe, montage
  2) moodstation doctor          # diagnostic
  3) moodstation sync            # première indexation + playlists

LLM local (recommandé) : voir README.md §3 (llama.cpp + modèle 0.5B Q4),
puis activez l'unité systemd llama-server fournie dans systemd/.
FIN
