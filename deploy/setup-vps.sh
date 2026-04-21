#!/bin/bash
#
# Быстрая установка на VPS (Ubuntu 22.04 / Debian 12)
# Запуск: sudo bash setup-vps.sh
#
set -euo pipefail

APP_DIR="/opt/usn-declaration"
NGINX_CONF="/etc/nginx/sites-available/usn-declaration"

echo "================================================"
echo "  Установка: Налоговая декларация ИП УСН 6%"
echo "================================================"
echo

# 1. Docker
if ! command -v docker &> /dev/null; then
    echo "[1/5] Устанавливаю Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
else
    echo "[1/5] Docker уже установлен ✓"
fi

# 2. Docker Compose plugin
if ! docker compose version &> /dev/null; then
    echo "[2/5] Устанавливаю Docker Compose plugin..."
    apt-get update -qq
    apt-get install -y -qq docker-compose-plugin
else
    echo "[2/5] Docker Compose уже установлен ✓"
fi

# 3. Nginx
if ! command -v nginx &> /dev/null; then
    echo "[3/5] Устанавливаю Nginx..."
    apt-get update -qq
    apt-get install -y -qq nginx
    systemctl enable nginx
else
    echo "[3/5] Nginx уже установлен ✓"
fi

# 4. Copy project
echo "[4/5] Копирую проект в ${APP_DIR}..."
mkdir -p "${APP_DIR}"
# Если скрипт запущен из папки deploy/, поднимемся на уровень
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

rsync -a --exclude='__pycache__' \
         --exclude='*.pyc' \
         --exclude='.git' \
         --exclude='data/usn.db' \
         --exclude='data/declarations/*' \
         --exclude='data/uploads/*' \
         "${PROJECT_DIR}/" "${APP_DIR}/"

# Create data dirs
mkdir -p "${APP_DIR}/data/uploads" "${APP_DIR}/data/declarations" "${APP_DIR}/uploads"

# 5. Configure nginx
echo "[5/5] Настраиваю Nginx..."
cp "${APP_DIR}/deploy/nginx.conf" "${NGINX_CONF}"
if [ -f /etc/nginx/sites-enabled/default ]; then
    rm -f /etc/nginx/sites-enabled/default
fi
ln -sf "${NGINX_CONF}" /etc/nginx/sites-enabled/usn-declaration
nginx -t && systemctl reload nginx

# 6. Start app
echo
echo "Собираю и запускаю контейнер..."
cd "${APP_DIR}"
docker compose up -d --build

echo
echo "================================================"
echo "  Готово!"
echo "  Приложение доступно по http://<IP-сервера>"
echo ""
echo "  Полезные команды:"
echo "    docker compose logs -f        # логи"
echo "    docker compose restart        # перезапуск"
echo "    docker compose down           # остановка"
echo "    docker compose up -d --build  # пересборка"
echo "================================================"
