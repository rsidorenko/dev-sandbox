#!/usr/bin/env bash
# Ограниченное ожидание готовности: постоянный отказ обязан остановить деплой.
set -euo pipefail

health_url="${1:-http://127.0.0.1/healthz}"
for attempt in {1..12}; do
  if curl --fail --silent --show-error --connect-timeout 3 --max-time 5 --header "Host: bravada-connect.ru" "$health_url" >/dev/null; then
    echo "Проверка готовности пройдена"
    exit 0
  fi
  if [ "$attempt" -lt 12 ]; then
    sleep 5
  fi
done
echo "Ошибка: приложение не прошло проверку готовности" >&2
exit 1
