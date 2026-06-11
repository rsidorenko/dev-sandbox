"""Check LTE panel auth: what's stored in 3x-ui DB + test login from prod container."""
import asyncio, os, re, json
import httpx

async def run():
    import asyncpg
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field
        row = await pool.fetchrow(
            "SELECT panel_url, panel_username, panel_password, COALESCE(encrypted_password,'') AS ep "
            "FROM vpn_servers WHERE id = 10")
        pw = decrypt_field(row["ep"]) if row["ep"] else row["panel_password"]
        panel = row["panel_url"].rstrip("/")
        print(f"Panel: {panel}")
        print(f"Username (DB): {row['panel_username']}")
        print(f"Password decrypted len: {len(pw)}")
    finally:
        await pool.close()

asyncio.run(run())
