#!/usr/bin/env bash
# Dump Frankfurt outbounds + routing + a fresh connection attempt test.
# Run via: sshpass ... ssh root@77.110.100.210 'bash -s' < this
echo "=== FRANKFURT OUTBOUNDS ==="
python3 -c "
import json
c = json.load(open('/usr/local/x-ui/bin/config.json'))
print('outbounds (order = priority, first = default):')
for o in c.get('outbounds', []):
    proto = o.get('protocol','?')
    tag = o.get('tag','?')
    extra = ''
    if proto == 'freedom': extra = '(DIRECT internet egress)'
    elif proto == 'blackhole': extra = '(BLACKHOLE - drops traffic!)'
    print(f'  [{tag}] {proto} {extra}')
print()
print('routing rules:')
for r in c.get('routing',{}).get('rules',[]):
    print(f'  {r.get(\"type\",\"?\")} inbound={r.get(\"inboundTag\",[])} out={r.get(\"outboundTag\",\"?\")} ip={r.get(\"ip\",[])[:3]} domain={r.get(\"domain\",[])[:2]}')
print()
print('domainStrategy:', c.get('routing',{}).get('domainStrategy','?'))
print()
print('vless inbound (443) sniffing/freedom path check:')
for ib in c.get('inbounds',[]):
    if ib.get('port')==443:
        print('  inbound tag:', ib.get('tag'))
"
echo ""
echo "=== test: does Frankfurt freedom egress work? (curl to internet from server) ==="
curl -s --max-time 8 https://api.ipify.org && echo " <- server egress OK" || echo "  server egress FAILED"
