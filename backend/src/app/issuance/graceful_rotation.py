"""Переход между поколениями ключей по подтверждённому трафику нового поколения."""

import asyncio
import logging
from datetime import UTC, datetime

from app.application.access_grant import access_grant
from app.issuance.fleet_lock import serialized_mutation
from app.issuance.key_identity import generation_accounts, key_email, vless_uuid_for_transport
from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderResult
from app.issuance.xui_client import XuiOutcome

_LOGGER = logging.getLogger(__name__)


class GracefulRotationMixin:
    async def rotation_status(self, internal_user_id: str) -> str:
        state = await self._key_state(internal_user_id)
        return state.get("vless_rotation_phase") or (
            "provisioning"
            if state.get("vless_rotation_pending")
            else "completed"
            if state.get("vless_first_traffic_at")
            else "idle"
        )

    async def _remember_key_email(self, uid: str, transport: str, email: str) -> None:
        if email == key_email(uid, transport):
            return
        await self._pool.execute(
            "INSERT INTO vless_key_emails (internal_user_id,transport_type,email) VALUES ($1,$2,$3)"
            " ON CONFLICT (email) DO NOTHING",
            uid,
            transport,
            email,
        )

    async def _new_generation_has_traffic(self, uid: str, state: dict) -> bool:
        """Отсутствие или ошибка счётчика не подтверждают переход."""

        async def bare_usage(client, configs):
            try:
                stats = await asyncio.wait_for(client.query_stats(), timeout=20)
                if not isinstance(stats, dict):
                    return False
                for cfg in configs:
                    email = key_email(uid, cfg.transport_type, state["vless_email_seed"])
                    usage = stats.get(email, {})
                    if int(usage.get("up", 0)) > 0 or int(usage.get("down", 0)) > 0:
                        return True
            except Exception:
                _LOGGER.warning("rotation_traffic_unavailable server=%s", client.server_id)
            return False

        async def panel_usage(client):
            try:
                email = key_email(uid, client.server_config.transport_type, state["vless_email_seed"])
                usage = await asyncio.wait_for(client.get_client_usage(email=email), timeout=20)
                return usage is not None and (usage[0] > 0 or usage[1] > 0)
            except Exception:
                _LOGGER.warning("rotation_traffic_unavailable server=%s", client.server_id)
                return False

        probes = [bare_usage(client, configs) for client, configs in await self.bare_xray_stats_hosts()]
        probes.extend(panel_usage(client) for client in await self._get_clients())
        return any(await asyncio.gather(*probes))

    async def _remove_previous_generation(self, uid: str, state: dict) -> bool:
        clients = await self._get_clients()
        bare = await self._get_bare_xray_clients()
        if not clients and not self._bare_xray_configs:
            return False
        if len(bare) != len(self._bare_xray_configs):
            return False
        success = True
        for client in clients:
            try:
                old_uuid = vless_uuid_for_transport(
                    uid, client.server_config.transport_type, state.get("vless_previous_key_seed")
                )
                old_email = key_email(uid, client.server_config.transport_type, state.get("vless_previous_email_seed"))
                actual_uuid = await client.resolve_client_uuid(email=old_email)
                if actual_uuid is not None:
                    old_uuid = actual_uuid
                result = await client.delete_client(user_uuid=old_uuid)
                success &= result.outcome in (XuiOutcome.SUCCESS, XuiOutcome.NOT_FOUND)
            except Exception:
                success = False
                _LOGGER.warning("rotation_retire_failed server=%s", client.server_id)
        for client in bare:
            try:
                email = key_email(uid, client.server_config.transport_type, state.get("vless_previous_email_seed"))
                success &= await client.remove_client(email=email)
            except Exception:
                success = False
                _LOGGER.warning("rotation_retire_failed server=%s", client.server_id)
        return success

    async def _clear_graceful_rotation(self, uid: str) -> None:
        await self._pool.execute(
            "UPDATE user_identities SET vless_rotation_pending=FALSE, vless_rotation_phase=NULL,"
            " vless_previous_key_seed=NULL, vless_previous_email_seed=NULL WHERE internal_user_id=$1",
            uid,
        )

    async def _remove_graceful_generations(self, uid: str, state: dict) -> bool:
        """Окончательное удаление подписки закрывает оба поколения."""
        previous = await self._remove_previous_generation(uid, state)
        current = await self._delete_user_unlocked(internal_user_id=uid)
        if previous and current.outcome == VlessProviderOutcome.SUCCESS:
            await self._clear_graceful_rotation(uid)
            return True
        return False

    async def _deactivate_graceful_generations(self, uid: str, state: dict) -> bool:
        """До конца обычного grace-периода панельные записи отключаем, не удаляем."""
        clients = await self._get_clients()
        bare = await self._get_bare_xray_clients()
        success = bool(clients or self._bare_xray_configs) and len(bare) == len(self._bare_xray_configs)
        both = dict(state, vless_rotation_phase="waiting")
        expiry = int(datetime.now(UTC).timestamp() * 1000)
        for client in clients:
            for email, key_uuid in generation_accounts(uid, client.server_config.transport_type, both).items():
                try:
                    key_uuid = await client.resolve_client_uuid(email=email) or key_uuid
                    result = await client.disable_client(user_uuid=key_uuid, email=email, expiry_ts=expiry)
                    success &= result.outcome in (XuiOutcome.SUCCESS, XuiOutcome.NOT_FOUND)
                except Exception:
                    success = False
        for client in bare:
            for email in generation_accounts(uid, client.server_config.transport_type, both):
                try:
                    success &= await client.remove_client(email=email)
                except Exception:
                    success = False
        return success

    async def _resume_graceful_rotation(self, uid: str, *, traffic_seen: bool = False) -> VlessProviderResult:
        state = await self._key_state(uid)
        grant = await access_grant(pool=self._pool, internal_user_id=uid)
        if grant is None:
            if await self._deactivate_graceful_generations(uid, state):
                return VlessProviderResult(VlessProviderOutcome.NOT_FOUND)
            return VlessProviderResult(VlessProviderOutcome.UNAVAILABLE)
        if state["vless_rotation_phase"] == "provisioning":
            result = await self._create_user_unlocked(internal_user_id=uid, require_all=True, **grant)
            if result.outcome != VlessProviderOutcome.SUCCESS:
                return result
            await self._pool.execute(
                "UPDATE user_identities SET vless_rotation_phase='waiting' WHERE internal_user_id=$1",
                uid,
            )
            self._invalidate_config_cache(uid)
            return result
        if state["vless_rotation_phase"] == "waiting" and traffic_seen:
            # Сначала сохраняем доказательство: рестарт счётчиков не отменит начатый отзыв.
            await self._pool.execute(
                "UPDATE user_identities SET vless_rotation_phase='retiring', vless_first_traffic_at=NOW()"
                " WHERE internal_user_id=$1",
                uid,
            )
            state["vless_rotation_phase"] = "retiring"
        if state["vless_rotation_phase"] == "retiring":
            if await self._remove_previous_generation(uid, state):
                await self._clear_graceful_rotation(uid)
        # Даже при недоступности одного сервера новые ключи остаются доступными.
        return await self._probe_user_config(uid) or VlessProviderResult(VlessProviderOutcome.UNAVAILABLE)

    async def process_key_rotations(self, *, limit: int = 20) -> None:
        rows = await self._pool.fetch(
            "SELECT internal_user_id FROM user_identities WHERE vless_rotation_pending"
            " ORDER BY vless_rotation_checked_at NULLS FIRST, internal_user_id LIMIT $1",
            limit,
        )
        for row in rows:
            uid = row["internal_user_id"]
            try:
                state = await self._key_state(uid)
                # Чтение статистики не блокирует оплаты и lifecycle других пользователей.
                seen = state.get("vless_rotation_phase") == "waiting" and await self._new_generation_has_traffic(
                    uid, state
                )
                await self._advance_rotation_locked(uid, state.get("vless_key_seed"), seen)
            except Exception:
                _LOGGER.warning("key_rotation_retry_pending user=%s", uid[:8])
            finally:
                await self._pool.execute(
                    "UPDATE user_identities SET vless_rotation_checked_at=NOW() WHERE internal_user_id=$1",
                    uid,
                )

    @serialized_mutation
    async def _advance_rotation_locked(self, uid: str, expected_seed: str | None, traffic_seen: bool) -> None:
        state = await self._key_state(uid)
        if not state.get("vless_rotation_pending") or state.get("vless_key_seed") != expected_seed:
            return
        async with asyncio.timeout(45):
            if state.get("vless_rotation_phase"):
                await self._resume_graceful_rotation(uid, traffic_seen=traffic_seen)
            else:
                await self._resume_legacy_rotation(uid)
