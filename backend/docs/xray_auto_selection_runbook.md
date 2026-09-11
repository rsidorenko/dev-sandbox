# Автовыбор Xray на существующих серверах

Инструмент `scripts.configure_xray_auto_selection` добавляет серверный `leastPing`-выбор
в существующий bare-Xray entry. Новый сервер и новый домен не требуются: клиент продолжает
подключаться к уже работающему VLESS/Reality или CDN-входу, а entry выбирает доступный
зарубежный выход с минимальной задержкой.

Обычный и LTE-пулы настраиваются отдельными профилями. Для обычного профиля публикация
использует `surface=both`, для LTE — `surface=happ`.

## Подготовка профиля

Создайте файл вне checkout, укажите существующий entry, его inbound и не менее двух уже
проверенных outbound. UUID межсерверных клиентов является credential и не должен попадать
в Git, MR, тикет или логи.

```json
{
  "profiles": [
    {
      "name": "normal",
      "host": "ENTRY_IP",
      "user": "SSH_USER",
      "key_path": "/absolute/path/to/ssh_key",
      "inbound_tags": ["ENTRY_INBOUND_TAG"],
      "outbound_prefix": "auto-normal-",
      "balancer_tag": "balancer-normal",
      "fallback_tag": "auto-normal-primary",
      "probe_url": "https://www.gstatic.com/generate_204",
      "probe_interval": "30s",
      "outbounds": [
        {
          "tag": "auto-normal-primary",
          "protocol": "vless",
          "settings": {"vnext": [{"address": "EXIT_1", "port": 443, "users": [{"id": "RELAY_UUID_1", "encryption": "none"}]}]},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {}}
        },
        {
          "tag": "auto-normal-secondary",
          "protocol": "vless",
          "settings": {"vnext": [{"address": "EXIT_2", "port": 443, "users": [{"id": "RELAY_UUID_2", "encryption": "none"}]}]},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {}}
        }
      ],
      "publish": {
        "server_ids": [999999],
        "label": "Автовыбор",
        "country_flag": "⚡",
        "country_code": "",
        "surface": "both"
      }
    }
  ]
}
```

Замените `999999` на ID выбранной entry-строки. В рабочем файле `server_ids` должны быть
числами, а `realitySettings` — полностью скопированы из уже проверенного межсерверного
outbound. Для LTE добавьте второй профиль с отдельными
тегами `auto-lte-*`, label `LTE Автовыбор`, флагом `📶` и `surface=happ`.

```bash
chmod 600 /secure/bravada-auto-selection.json
```

Перед работой проверьте строки БД и live-конфиг read-only командами. Не активируйте второй
LTE origin только ради пула: сначала он должен пройти собственную проверку CDN, gRPC и egress.

## Dry-run

Без `--apply` инструмент ничего не пишет и не перезапускает:

```bash
cd /opt/bravada/backend
python -m scripts.configure_xray_auto_selection \
  --config /secure/bravada-auto-selection.json --profile normal
python -m scripts.configure_xray_auto_selection \
  --config /secure/bravada-auto-selection.json --profile lte
```

Dry-run должен найти все inbound-теги и показать `candidates>=2`. Содержимое outbound и UUID
не выводятся.

## Применение и публикация

Запускайте профили по одному. `--publish` допустим только с `--apply`: сначала сохраняются
live-users, проверяется новый конфиг, создаётся backup и перезапускается Xray; только затем
меняются label/surface и fragment custom `link_template` в `vpn_servers`.

```bash
export DATABASE_URL='значение из production environment'
python -m scripts.configure_xray_auto_selection \
  --config /secure/bravada-auto-selection.json --profile normal --apply --publish
```

После обычного профиля обновите подписку в Happ и Karing: «⚡ Автовыбор» должен быть первым.
После LTE-профиля обновите Happ: следом должен отображаться «📶 LTE Автовыбор».

Проверьте два разных egress по очереди, временно отключая по одному кандидату в maintenance
окне. Переключение должно произойти после следующего `probe_interval`; при отсутствии всех
результатов используется `fallbackTag`.

## Откат

Для каждого entry создаётся backup:

```bash
sudo cp /usr/local/etc/xray/config.json.pre-auto-selection.bak \
  /usr/local/etc/xray/config.json
sudo /usr/local/bin/xray run -test -config /usr/local/etc/xray/config.json
sudo systemctl restart xray
systemctl is-active xray
```

Верните прежние `label`, `country_flag`, `country_code`, `client_surface` и fragment
`link_template` у опубликованных строк отдельным точечным `UPDATE`. Не отключайте строку:
это также отключит lifecycle/reconcile пользователей данного inbound.

## Ограничения

- Измеряется путь entry → exit, а не телефон → entry.
- `leastPing` выбирает минимальную задержку среди живых выходов, но не измеряет полосу.
- Используется единый JSON-конфиг Xray. Не переводите эту схему в `-confdir` без отдельного
  E2E-теста маршрутизации.
