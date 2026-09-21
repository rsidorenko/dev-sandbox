#!/usr/bin/env bash
# Копируем только vhost Mini App; новый конфиг в артефакте имеет приоритет.
set -euo pipefail

source_dir="${1:?Нужен исходный каталог nginx/conf.d}"
target_dir="${2:?Нужен целевой каталог nginx/conf.d}"
mkdir -p "$target_dir"

for config_name in api.bravada-connect.ru.conf chat.bravada-connect.ru.conf bravada-gpt-root.conf; do
  if [[ -f "$source_dir/$config_name" && ! -e "$target_dir/$config_name" ]]; then
    cp -p "$source_dir/$config_name" "$target_dir/$config_name"
  fi
done
