#!/usr/bin/env bash
# Check ALL servers for the empty-condition blackhole routing rule.
# Runs on the target server. Usage: ssh <server> 'bash -s' < this
echo "=== $(hostname) routing rules ==="
python3 -c "
import json
c = json.load(open('/usr/local/x-ui/bin/config.json'))
rules = c.get('routing',{}).get('rules',[])
print(f'total rules: {len(rules)}')
for i, r in enumerate(rules):
    ip = r.get('ip',[]); dom = r.get('domain',[]); ibt = r.get('inboundTag',[])
    proto = r.get('protocol',[]); net = r.get('network','')
    out = r.get('outboundTag','?')
    empty = not ip and not dom and not ibt and not proto and not net
    flag = '  <<< EMPTY CATCH-ALL -> ' + out + ' (BLACKHOLE BUG!)' if empty else ''
    print(f'  [{i}] out={out} ip={ip[:2]} domain={dom[:2]} inbound={ibt} proto={proto} net={net!r}{flag}')
print('outbounds:')
for o in c.get('outbounds',[]):
    print(f'  [{o.get(\"tag\",\"?\")}] {o.get(\"protocol\",\"?\")}')
"
