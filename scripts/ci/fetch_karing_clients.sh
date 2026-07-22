#!/usr/bin/env bash
# Подгрузка свежих клиентских сборок Karing (Android APK + Windows .exe) из публичного
# апстрима KaringX/karing и публикация их в /opt/bravada-downloads (nginx отдаёт /downloads/).
#
# Порт шагов deploy.yml "Fetch latest Karing APK/exe" под GitLab (без gh CLI, через curl).
# Анонимный GitHub API: лимит 60 запросов/час/IP — для деплоя (1-2 запроса) достаточно.
#
# Использование: fetch_karing_clients.sh <ssh-target>
#   <ssh-target> например: romtan@109.120.178.49
# Шаг нефатальный: при любой ошибке exiting 0, старые APK/манифесты остаются на сервере.
set -euo pipefail

SSH_TARGET="${1:-}"
if [ -z "$SSH_TARGET" ]; then
  echo "fetch_karing_clients: SSH target not provided — skip" >&2
  exit 0
fi

API="https://api.github.com/repos/KaringX/karing/releases/latest"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Resolving latest Karing release..."
RELEASE_JSON="$TMP/release.json"
if ! curl -fsSL "$API" -o "$RELEASE_JSON"; then
  echo "fetch_karing_clients: GitHub API fetch failed — skip" >&2
  exit 0
fi

TAG="$(python3 -c "import json,sys; print(json.load(open('$RELEASE_JSON')).get('tag_name',''))" 2>/dev/null || true)"
if [ -z "$TAG" ]; then
  echo "fetch_karing_clients: cannot parse tag_name — skip" >&2
  exit 0
fi
VERSION="${TAG#v}"
echo "Latest Karing release: $TAG (version $VERSION)"

APK_URL="$(python3 -c "
import json
r = json.load(open('$RELEASE_JSON'))
for a in r.get('assets', []):
    n = a.get('name','')
    if 'android' in n and 'arm64-v8a' in n and n.endswith('.apk'):
        print(a.get('browser_download_url','')); break
" 2>/dev/null || true)"
EXE_URL="$(python3 -c "
import json
r = json.load(open('$RELEASE_JSON'))
for a in r.get('assets', []):
    n = a.get('name','')
    if 'windows' in n and 'x64' in n and n.endswith('.exe'):
        print(a.get('browser_download_url','')); break
" 2>/dev/null || true)"

ssh "$SSH_TARGET" 'mkdir -p /opt/bravada-downloads'

publish() {  # publish <url> <filename> <manifest_name>
  local url="$1" fname="$2" manifest="$3"
  if [ -z "$url" ]; then echo "  $manifest: no asset found — skip"; return; fi
  echo "  $fname <- $url"
  if ! curl -fsSL "$url" -o "$TMP/$fname"; then echo "  download failed — skip"; return; fi
  local size; size="$(stat -c%s "$TMP/$fname" 2>/dev/null || echo 0)"
  python3 -c "import json; json.dump({'version':'$VERSION','filename':'$fname','url':'/downloads/$fname','size_bytes':int('$size' or 0)}, open('$TMP/$manifest','w'))"
  scp -q "$TMP/$fname" "$SSH_TARGET:/opt/bravada-downloads/"
  scp -q "$TMP/$manifest" "$SSH_TARGET:/opt/bravada-downloads/"
}

APK_FNAME="$(basename "$APK_URL")"
EXE_FNAME="$(basename "$EXE_URL")"
publish "$APK_URL" "$APK_FNAME" latest-android.json
publish "$EXE_URL" "$EXE_FNAME" latest-windows.json

# Чистим устаревшие сборки (кроме только что залитых) — нефатально.
ssh "$SSH_TARGET" "
  cd /opt/bravada-downloads || exit 0
  rm -f karing-android.apk
  for f in karing_*_android_*.apk; do [ -e \"\$f\" ] || continue; [ \"\$f\" = '$APK_FNAME' ] || rm -f \"\$f\"; done
  for f in karing_*_windows_*.exe;   do [ -e \"\$f\" ] || continue; [ \"\$f\" = '$EXE_FNAME' ] || rm -f \"\$f\"; done
" 2>/dev/null || true

echo "fetch_karing_clients: done"
exit 0
