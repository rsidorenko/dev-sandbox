# Миграция GitHub → GitLab.com

Контекст: аккаунт GitHub `rsidorenko` заблокирован (suspended). Полный репозиторий
есть локально (все ветки + история), поэтому миграция на GitLab ничего не теряет и
не зависит от GitHub. CI/CD переписан с GitHub Actions на `.gitlab-ci.yml`.

## Что перенесено

- `.gitlab-ci.yml` — полный порт: backend (lint / unit / postgres-smoke / retention-integration), codegraph, frontend (lint / typecheck / build / unit / e2e / gate), deploy.
- `scripts/ci/fetch_karing_clients.sh` — подгрузка Karing APK/.exe из публичного апстрима (без аккаунта GitHub).
- Секреты и deploy-логика — ниже.

## Шаги (выполняет пользователь на gitlab.com)

1. **Репозиторий создан** (приватный `dev-sandbox`). ✅
2. **Добавить remote и запушить** (даёт Claude по запросу кредов, либо сами):
   ```bash
   git remote add gitlab https://gitlab.com/<ВАШ_НИК>/dev-sandbox.git
   git push -u gitlab main            # основная ветка с .gitlab-ci.yml
   git push gitlab feat/promo-codes-domain feat/promo-admin-backend feat/promo-admin-frontend
   ```
   > Чтобы на `main` был и админ-код, и CI — см. «Консолидация» ниже.
3. **Settings → Repository → Default branch** = `main`.
4. **Settings → CI/CD → Variables** — для стадии `deploy` (CI-гейты работают и без них):
   - `SERVER_HOST` — хост/IP прод-сервера.
   - `SERVER_USER` — ssh-пользователь деплоя.
   - `SERVER_SSH_KEY` — **тип File**, приватный ключ деплоя (тот же, что был `secrets.SERVER_SSH_KEY` на GitHub).
   - (для операторских тогглов, опционально) `FIELD_ENCRYPTION_KEY`, `INTERNAL_API_SECRET`, `FRANKFURT_SSH_PASS`, `HELSINKI_SSH_PASS`, `LA_SSH_KEY`.
   - Отметьте **Masked** где применимо; `SERVER_SSH_KEY` — тип File (не маскируется, но не пишется в логи).
5. **Settings → Repository → Protected branches**: `main` — maintainers; **Required** для мерджа: `backend:postgres-smoke`, `backend:lint`, `frontend:gate`, `codegraph:check` (и др. по вкусу).
6. Первый пуук CI прогонит гейты на `main`. `deploy` запустится автоматически на push в `main` (если заданы SERVER_*), либо вручную (Run pipeline → job `deploy`).

## ⚠️ Приватный репо = лимит CI-минут

На бесплатном тарифе GitLab.com у **приватных** проектов ограничены CI-минуты
(~400 мин/мес, в 2025 ужесточено для части неймспейсов). Этот CI тяжеловат
(postgres-services, Playwright, build). Если упрётесь в лимит — варианты:
- сделать проект публичным (CI бесплатно, как было на GitHub);
- поднять **self-hosted gitlab-runner** на прод-сервере (безлимитно, деплой локально).

## Консолидация main (чтобы админ-панель была на main)

Сейчас Фазы 0-2 лежат на отдельных локальных ветках. Чтобы на GitLab `main` сразу
был и код админки, и `.gitlab-ci.yml`:
```bash
git checkout main
git merge --no-ff feat/promo-codes-domain feat/promo-admin-backend feat/promo-admin-frontend chore/gitlab-migration
# разрешить конфликты (если есть), прогнать: cd backend && python -m pytest -q && ruff check src tests
git push gitlab main
```
Либо пушить ветки как есть и мерджить через GitLab MR.

## Что НЕ переносил (инкремент, по запросу)

- 13 операторских dispatch-воркфлоуов (diag/reprovision/relay/sync/upgrade/…) —
  они не блокируют CI/деплой. Перенесу в manual-джобы отдельным коммитом.
- Никшевые тогглы `deploy.yml` (reissue/cleanup/rebuild/fix_panel_url/…). Ядро деплоя
  (артефакт + swap + docker compose + таймеры + Karing) перенесено.
