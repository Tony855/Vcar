#!/bin/bash
set -e
cd /root/vcar-v2

echo "🔄 重启 vcar-bot..."
systemctl restart vcar-bot

echo "⏳ 等待 bot ready（VPS CPU 限制约需 10-15 秒）..."
for i in $(seq 1 300); do
    journalctl -u vcar-bot --since "20 seconds ago" --no-pager 2>/dev/null \
        | grep -q "Run polling for bot" \
        && { echo "✅ bot ready，耗时 $((i*100))ms"; exit 0; }
    sleep 0.1
done

echo "❌ 30 秒内未 ready，检查日志："
journalctl -u vcar-bot -n 30 --no-pager
exit 1
