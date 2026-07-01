-- 053: per-server custom VLESS link template.
-- For non-standard transports the bot's _build_vless_link cannot assemble (e.g. the h1cloud
-- "обход белых списков" CDN key: xhttp+TLS via a CDN front node67.safetunn.shop with heavy
-- xmux/xPadding obfuscation in the `extra` param). When non-empty, the bot emits this template
-- verbatim with the literal {UUID} placeholder substituted by the per-user VLESS UUID, instead of
-- building the link from transport/reality fields. Empty (default) = standard per-transport builder.
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS link_template TEXT NOT NULL DEFAULT '';
