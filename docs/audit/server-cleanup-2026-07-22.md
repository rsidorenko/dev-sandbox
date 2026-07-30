# Server Cleanup Audit — 2026-07-22

**Scope:** тотальный аудит 10 серверов Bravada VPN (5 RU-entries + 5 foreign-egress).
**Принцип:** юзеры подключаются ТОЛЬКО к RU-ноге (entries), cascade идёт к foreign-egress.
К egress напрямую подключаться не должны. Всё лишнее — кандидат на удаление.

**Аудит выполнен:** 2026-07-22. Файл — living document, обновлять статусы по мере фикса.

---

## Серверы

### RU-нога (entries, user-facing)
| IP | Label | Type | Provider |
|----|-------|------|----------|
| 85.208.87.187 | Германия | bare_xray | cloud.ru |
| 176.108.250.237 | Финляндия | bare_xray | cloud.ru |
| 89.169.166.161 | Литва (+YT surface) | bare_xray | Yandex |
| 158.160.26.18 | YouTube | bare_xray | Yandex |
| 89.169.139.153 | США | x-ui (3x-ui) | Yandex relay |
| 158.160.149.191 | CDN-bypass (id=34) | bare_xray | Yandex — **DO NOT TOUCH** (вне аудита) |

### Зарубежная нога (egress, cascade-only)
| IP | Label | Type |
|----|-------|------|
| 77.110.100.210 | Frankfurt | xray + x-ui (dual) |
| 45.137.60.80 | Helsinki | x-ui — **SSH недоступен** (нет пароля) |
| 45.89.60.37 | Albania | x-ui |
| 2.59.162.163 | Lithuania | x-ui |
| 77.110.125.209 | US egress | x-ui |

**Доступ:** entries (cloudru_key.pem/romtan, кроме US=us_entry_key.pem); egress root+pass в `D:/tmp/{frankfurt_creds,albania,lithuania_creds,us_egress}.env`. SSH-runner: `MSYS_NO_PATHCONV=1 SSH_HOST=… python D:/tmp/ssh_run.py`.

---

## 🔴 PRIORITY 1 — security / нарушение архитектуры

### P1-01 · Lithuania egress: vision-flow клиентов на :443 — УБРАНЫ
- **Сервер:** 2.59.162.163
- **Решение (2026-07-22):** вариант 2 — привести к cascade-only. `UPDATE clients SET flow='' + UPDATE client_inbounds SET flow_override='' + inbound settings.clients` для inbound_id=1 (in-443-tcp), 34 клиента. Backup `.pre-vision.bak`. Restart x-ui.
- **Результат:** `:443 in-443-tcp` теперь `flows=['']` (all empty, 30 clients). Cascade Lithuania-entry(empty-outbound)→egress(empty-client) — matching, работает.
- **НЕ тронут:** `:10444 in-10444-origin` (CDN-bypass, `from-origin-cdn 77cfa287`, vision — legitimate cascade-leg от strimingset origin).
- **Прямые user-ключи к egress нейтрализованы** (flow-mismatch для vision-клиентов).
- **Статус:** ✅ done

### P1-02 · DE entry: PasswordAuth=yes (cloud-init override) — ОСТАВЛЕН
- **Сервер:** 85.208.87.187
- **Находка:** `50-cloud-init.conf: PasswordAuthentication yes`. По решению оператора **пароль оставлен специально** (не SSH-key).
- **Статус:** ⏭️ skip by design

### P1-03 · Albania + Lithuania egress: password SSH + ufw off
- **Серверы:** 45.89.60.37, 2.59.162.163
- **Пароль:** оставлен by design. **Firewall: ✅ ufw установлен + включён** (Albania: 22/443/4433/80 public, 25852+2096 from main; Lithuania: 22/443/8443/10444/80 public, 38441+2096 from main).
- **Статус:** ✅ firewall done / ⏭️ password skip by design

### P1-04 · x-ui панели публично открыты на egress — ЗАКРЫТЫ
- **Серверы/порты:** Albania `25852`, Lithuania `38441`, US-egress `4026`, + `2096` (x-ui sub) на всех 3.
- **Фикс:** ufw `ALLOW FROM 109.120.178.49` (main only) для всех panel-портов. Frankfurt 2053 уже был from-main (эталон).
- **Статус:** ✅ done

### P1-05 · US entry: панель 2053 публично — ЗАКРЫТА
- **Сервер:** 89.169.139.153
- **Фикс:** `ufw delete allow 2053/tcp` → `ufw allow from 109.120.178.49 to any port 2053`. Теперь `2053/tcp ALLOW 109.120.178.49`.
- **Статус:** ✅ done

### P1-06 · Entries: ufw inactive (DE/FI/LT/YT) — ВКЛЮЧЁН
- **Серверы:** 85.208.87.187 (DE), 176.108.250.237 (FI), 89.169.166.161 (LT), 158.160.26.18 (YT).
- **Фикс:** ufw enabled. Rules: 22 (SSH) + 443/8443 (VPN) + 80 (certbot). LT доп.: +4443, +9443.
- **Статус:** ✅ done

---

## 🟡 PRIORITY 2 — bloat / cleanliness

### P2-07 · snapd — PURGED
- **Серверы:** DE, FI, Albania, Lithuania-egress.
- **Фикс:** `apt purge snapd` + rm /snap /var/snap /var/lib/snapd. DE 58→51%, FI 67→60%, egress ~20%.
- **Статус:** ✅ done

### P2-08 · Desktop-сервисы — DISABLED
- **Серверы:** DE, FI (cloud.ru entries).
- **Фикс:** disable+mask ModemManager, fwupd, packagekit, udisks2, upower.
- **Статус:** ✅ done

### P2-09 · /var/log — VACUUMED + cap
- **Серверы:** Frankfurt (1.2G→355M), FI (1.2G→289M, −880M), Albania (418M→217M), Lithuania-egress (610M→480M).
- **Фикс:** `journalctl --vacuum-size=100M --vacuum-time=7d` + journald drop-in `/etc/systemd/journald.conf.d/size-cap.conf` (SystemMaxUse=200M, MaxFileSec=7day) + btmp truncate + delete old x-ui .prev/.gz.
- **Статус:** ✅ done

### P2-10 · fail2ban — на всех entries
- **Вариант А (решение оператора):** поставить на все 5 entries.
- **Фикс:** apt install fail2ban на FI/LT/YT (DE/US уже имели). Теперь на всех 5 entries + всех egress.
- **Статус:** ✅ done

---

## 🟢 PRIORITY 3 — разобраться / решение с оператором

### P3-11 · Frankfurt — dual xray+x-ui — РЕШЕНО (мусор убит, dual оставлен)
- **Сервер:** 77.110.100.210
- **Что было:** 3 xray-процесса — (1) standalone `xray.service` `:10444 from-origin` = **CDN-bypass Германия** (gRPC per-user, нужен), (2) x-ui's xray `:443/:8443` = cascade (нужен), (3) **PID 70385 `/tmp/h1_direct.json`** = leftover от h1cloud (dest 193.233.126.96:25103, h1cloud удалён PR #428), localhost:10861, 0 conns, PPID=1 (orphaned).
- **Фикс:** `kill 70385` + `rm /tmp/h1_direct.json`. Dual xray+x-ui **оставлен** (архитектурно правильно: CDN-bypass отдельно через standalone xray с gRPC-API :10085, cascade через x-ui с панелью).
- **Статус:** ✅ done (мусор убит, CDN-bypass :10444 не тронут — PID 194203 active)

### P3-12 · Albania `:4433 in-4433-yar` — УДАЛЕНО
- **Сервер:** 45.89.60.37
- **Что было:** inbound на `:4433`, клиент `yar-relay-al` (cascade-relay). 0 live conns, access-log пуст — клиент реально работает через `:443`, не :4433 (дубликат/legacy).
- **Фикс:** DELETE inbound + client_inbounds + client_traffics. ufw `:4433` closed. Backup `.pre-4433.bak`.
- **Статус:** ✅ done

### P3-13 · Helsinki 45.137.60.80 — РЕШЕНО
- **Сервер:** Helsinki egress.
- **Что было:** SSH-доступ неизвестен (пароль устарел).
- **Фикс:** оператор дал пароль. Аудит показал: `:443` имеет 32 vision-клиента (как Lithuania раньше) + ufw active БЕЗ правил + панели 2053/2096 публично.
- **Применено:** ufw (22/443/8443/80 public, 2053+2096 → main only) + vision cleanup (`:443` 32 клиента → empty, cascade-only) + backup `.pre-vision.bak`.
- **Статус:** ✅ done

---

## ✅ НЕ трогать (норма, найдено при аудите)

- **UDP random high ports** на Frankfurt/Lithuania egress = `xray-linux-amd6` QUIC/xhttp outbound ephemeral sockets. Норма.
- **11111 / 10861 / 62789 на localhost** = x-ui/xray internal API (dokodemo-door). Норма.
- **nginx sites** (`alb-camo`, `reality-dest`, `le-tls`, `nkk`, `default`) = camo для reality TLS + LE-cert. Нужно.
- **fail2ban на egress** = защита SSH (btmp 28-31M = brute-force отбивается). Оставить.
- **PermitRootLogin yes на egress** = нужно для ops, при key-only auth безопасно.
- **US entry UFW** = эталон (active, правила разумные, кроме P1-05 2053).
- **xray 26.6.22** everywhere (единая версия).
- **canonical routing** (применён 2026-07-21, см. [[canonical-routing-2026-07]] в memory).

---

## Порядок применения (предложение)

1. **Сначала P1** (security/архитектура): P1-02 (DE PasswordAuth) → P1-03 (egress password+fw) → P1-04/05 (панели) → P1-01 (Lithuania vision) → P1-06 (entries ufw).
2. **Потом P2** (bloat): snapd purge → desktop-disable → log-vacuum.
3. **P3 — после решения оператора** (Frankfurt dual, Albania 4433, Helsinki пароль).

**Безопасность:** все изменения через backup + verify. SSH-fixes — СНАЧАЛА убедиться что key-auth работает, потом отключать password. ufw — СНАЧАЛА добавить правила, потом enable.

---

## P1 итог (2026-07-22)

| ID | Что | Статус |
|----|-----|--------|
| P1-01 | Lithuania egress 35 vision-клиентов | ✅ done (cascade-only) |
| P1-02 | DE PasswordAuth | ⏭️ skip by design |
| P1-03 | Albania/Lithuania egress fw | ✅ ufw done / пароль skip |
| P1-04 | egress panels публично | ✅ done (all → main only) |
| P1-05 | US entry 2053 | ✅ done (→ main only) |
| P1-06 | entries ufw inactive | ✅ done (all 4 enabled) |

## P2 итог (2026-07-22)

| ID | Что | Статус |
|----|-----|--------|
| P2-07 | snapd purge (DE/FI/Albania/LT-egress) | ✅ done |
| P2-08 | desktop disable (DE/FI) | ✅ done |
| P2-09 | /var/log vacuum + journald cap | ✅ done |
| P2-10 | fail2ban на всех 5 entries | ✅ done (вариант А) |

## P3 итог (2026-07-22)

| ID | Что | Статус |
|----|-----|--------|
| P3-11 | Frankfurt dual xray+x-ui | ✅ мусор убит, dual оставлен (архитектурно правильно) |
| P3-12 | Albania :4433 legacy inbound | ✅ удалён |
| P3-13 | Helsinki SSH + аудит | ✅ пароль получен, ufw+vision+panel применено |

**Применено:** ufw на 7 серверов (4 entries + 3 egress; Frankfurt уже был + US-entry уже был), панели привязаны к main, US-entry 2053 закрыт. **На работу юзеров не повлияло** — VPN-порты (443/8443/10444/4443/9443) открыты, SSH сохранён.

---

*Последнее обновление: 2026-07-22. Ведётся по мере фикса.*
