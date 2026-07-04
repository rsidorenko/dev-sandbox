# SSL / Let's Encrypt auto-renewal runbook

## Purpose
Make TLS for `bravada-connect.ru` self-maintaining so the cert never silently expires,
and keep it on a **DV (domain-validation)** CA that is **not** subject to the
GlobalSign sanctions-driven revocation wave.

This runbook does **not** address the iOS "fraudulent site" warning (that is a Google
Safe Browsing domain-reputation flag — see the iOS fraud-warning plan). It only secures
the certificate lifecycle.

## Background: the two separate problems (do not confuse them)

1. **iOS "fraudulent/phishing site" warning** → Google Safe Browsing flags the **domain**.
   The cert is irrelevant. Fixed via Google Search Console → Security Issues → Request Review.
2. **Certificate revocation wave (habr.com/ru/news/1047084, 2026-06-13)** → **GlobalSign**
   (GMO Internet Group, EU-sanctions-bound) began forced revocation of **OV/EV** certs
   issued to Russian organizations, per mandatory CA/Browser Forum sanctions screening
   (in force 2026-05-04). **DV certs are not affected** (no organization to screen).
   `bravada-connect.ru` runs a **DV Let's Encrypt E7** cert (verified live) → **safe**.

## The defect this runbook fixes

`docker-compose.prod.yml` has a `certbot` service that renews into a **named volume**
`certs:/etc/letsencrypt`, but **nginx reads `/opt/bravada/ssl/`** (a bind mount), not that
volume. So:
- The compose `certbot` service is effectively dead (renews into a volume nothing reads).
- The cert in `/opt/bravada/ssl/` is rotated **manually**.
- LE certs expire in 90 days → without manual action the site breaks TLS.

Current cert (as of 2026-07-04): Let's Encrypt E7, `notAfter` 2026-08-20. Fix before then.

## Recommended fix: host-level certbot with a renew hook

Host-level certbot is the robust, standard pattern: it survives `deploy.yml` (which wipes
`/opt/bravada` but preserves host `/etc/letsencrypt` and `/opt/bravada/ssl/`) and can reload
the nginx container directly.

### Prereq: make the ACME webroot host-accessible

nginx serves `/.well-known/acme-challenge/` from the container path `/var/www/certbot`,
backed by the `certbot-www` **named volume**. Host certbot needs a real host path.

In `docker-compose.prod.yml`, switch `certbot-www` from a named volume to a bind mount
(two lines, identical change for both services):

```diff
   nginx:
     volumes:
       - ./nginx/conf.d:/etc/nginx/conf.d:ro
       - ./ssl:/etc/nginx/ssl:ro
-      - certbot-www:/var/www/certbot:ro
+      - ./certbot-www:/var/www/certbot:ro
       - certs:/etc/letsencrypt:ro
   ...
   certbot:
     volumes:
-      - certbot-www:/var/www/certbot
+      - ./certbot-www:/var/www/certbot
       - certs:/etc/letsencrypt
```

Then apply and create the dir:
```bash
cd /opt/bravada
mkdir -p certbot-www
docker compose -f docker-compose.prod.yml up -d --force-recreate nginx certbot
```

(The `certbot` compose service becomes vestigial once host certbot owns renewal; you may
leave it or remove it — but do not let both manage the same `/etc/letsencrypt`. Host
certbot uses host `/etc/letsencrypt`; the compose service uses the `certs` volume —
separate stores, no direct conflict.)

### Run the setup script (once, as root)

```bash
cd /opt/bravada
sudo ./backend/scripts/setup_letsencrypt_autorenew.sh \
    --domain bravada-connect.ru \
    --webroot /opt/bravada/certbot-www \
    --email ops@your-contact.example
```

What it does (idempotent):
1. Installs `certbot` if missing (apt).
2. Installs a renewal deploy-hook at `/etc/letsencrypt/renewal-hooks/deploy/bravada-nginx-sync.sh`
   that copies `fullchain.pem`/`privkey.pem` into `/opt/bravada/ssl/` and sends nginx a
   graceful `HUP` (no downtime).
3. Issues the cert via webroot (`bravada-connect.ru` + `www.bravada-connect.ru`) if absent.
4. Enables `certbot.timer`.

## CA policy (prevent the next revocation wave)

- **Use DV CAs only**: Let's Encrypt, ZeroSSL, Buypass. No organization screening → not
  subject to OFAC/EU sanctions revocation.
- **Avoid OV/EV** from sanction-bound CAs (GlobalSign/GMO, DigiCert, Sectigo, Entrust,
  Certum) — they must screen the owning organization and can revoke RU legal entities, as
  GlobalSign did on 2026-06-13.
- **Do not** put the National UC of MinTsifry / "Russian Trusted CA" on a public
  (client-facing) domain — Apple does not trust them, which reintroduces the iOS
  "This Connection Is Not Private" error.

## Audit (apply to every public TLS endpoint, not just the landing)

For each public host/panel the project exposes to clients, confirm the issuer is a DV CA:
```bash
echo Q | openssl s_client -connect <host>:443 -servername <host> | openssl x509 -noout -issuer
# expect: O=Let's Encrypt (or ZeroSSL/Buypass). NOT GlobalSign/DigiCert/Sectigo.
```
Targets to check: `bravada-connect.ru` (✅ LE E7), any client-facing 3x-ui panel domain
(see `backend/docs/vpn_server_setup_guide.md` — panels may use Cloudflare-origin or
self-signed), any admin/internal host reachable from iOS.

## Verification

```bash
certbot certificates                                   # notAfter ~+90d, DV
ls -l /opt/bravada/ssl/fullchain.pem                   # synced copy, recent mtime
systemctl list-timers certbot.timer                    # next renewal scheduled
certbot renew --dry-run                                # exercises the deploy-hook + reload
echo Q | openssl s_client -connect bravada-connect.ru:443 -servername bravada-connect.ru -status \
  | grep -iE "issuer=|OCSP Response Status"            # LE issuer; OCSP stapling OK
```

## Risks
- The bind-mount change (`certbot-www`) must be applied before the script's webroot issuance
  or `certonly` will fail to validate. Run the diff above first.
- Host certbot + the compose `certbot` service are two renewal mechanisms — pick one (host,
  recommended) and ignore/remove the other to avoid confusion.
- A bad renewal deploy-hook could leave nginx serving a stale cert. The script's hook falls
  back to a bare `docker kill -s HUP $(docker ps -qf name=nginx)`; verify with
  `certbot renew --dry-run` + the openssl check above.
