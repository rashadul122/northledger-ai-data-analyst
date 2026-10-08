#!/usr/bin/env bash
# A read-only snapshot of the public DNS answers for the halosyncs.com names the NorthLedger launch must not disturb.
# Run it BEFORE the custom domain is attached and AFTER; the two outputs may differ ONLY in the northledger lines.
#   tools/dns_snapshot.sh > /somewhere/dns-before.txt ; (attach the domain) ; tools/dns_snapshot.sh > /somewhere/dns-after.txt
#   diff /somewhere/dns-before.txt /somewhere/dns-after.txt        # expect: only "northledger" lines change
# Uses dig against Cloudflare's resolver (1.1.1.1) and the zone's own name servers (a stale cache cannot hide a change).
# It reads public records only. It cannot see records that do not answer on the names below (the dashboard's DNS
# export, DNS > Records > Export, is the complete list: keep one export from before the attach too).
set -u
ZONE="${ZONE:-halosyncs.com}"
NS_LIST="$(dig +short NS "$ZONE" @1.1.1.1 | sed 's/\.$//' | sort)"
echo "zone: $ZONE   name servers: $(echo $NS_LIST)"
for resolver in 1.1.1.1 $(echo "$NS_LIST" | head -2); do
  echo "---- as answered by $resolver"
  for spec in "$ZONE NS" "$ZONE A" "$ZONE AAAA" "$ZONE MX" "$ZONE TXT" "$ZONE CAA" \
              "www.$ZONE A" "www.$ZONE AAAA" "www.$ZONE CNAME" \
              "api.$ZONE A" "api.$ZONE AAAA" "api.$ZONE CNAME" \
              "northledger.$ZONE A" "northledger.$ZONE AAAA" "northledger.$ZONE CNAME"; do
    set -- $spec
    printf '%-34s %-6s ' "$1" "$2"
    dig +short +time=5 +tries=2 "$1" "$2" @"$resolver" | sort | tr '\n' ' '
    echo
  done
done
