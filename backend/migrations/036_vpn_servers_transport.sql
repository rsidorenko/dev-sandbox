-- Add transport type to vpn_servers to support multiple transports (tcp, xhttp, grpc).
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS transport_type TEXT NOT NULL DEFAULT 'tcp';
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS grpc_service_name TEXT NOT NULL DEFAULT '';
