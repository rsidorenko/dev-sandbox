-- Remove flag emoji prefix from TCP server label.
-- "🇫🇮 Хельсинки 🔒 1.0" → "Хельсинки 🔒 1.0"
UPDATE vpn_servers SET label = 'Хельсинки 🔒 1.0' WHERE id = 1 AND transport_type = 'tcp';
