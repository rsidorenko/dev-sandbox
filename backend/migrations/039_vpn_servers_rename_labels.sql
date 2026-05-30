-- Rename VPN server labels after gRPC removal.
-- TCP: "Хельсинки-1" → "Хельсинки 🇫🇮 1.0"
-- CDN: "Хельсинки ☁️4.0" → "Хельсинки ☁️3.0"
UPDATE vpn_servers SET label = 'Хельсинки 🇫🇮 1.0' WHERE id = 1 AND transport_type = 'tcp';
UPDATE vpn_servers SET label = 'Хельсинки ☁️3.0' WHERE id = 6 AND transport_type = 'cdn';
