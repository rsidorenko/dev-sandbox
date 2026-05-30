-- Remove gRPC transport (deprecated in xray-core, redundant with XHTTP).
DELETE FROM vpn_servers WHERE transport_type = 'grpc';
