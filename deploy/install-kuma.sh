#!/usr/bin/env bash
# Fresh native install only. Does not upgrade system Node, install Docker or edit a firewall.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run as root on the monitoring host'; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo 'This pinned installer is for x86_64'; exit 1; }
[[ ! -e /opt/uptime-kuma && ! -e /etc/systemd/system/sysmonitor-kuma.service ]] || { echo 'Existing install found; stop and inspect'; exit 1; }
! ss -H -lnt '( sport = :3001 )' | grep -q . || { echo 'Port 3001 is occupied'; exit 1; }
for cmd in curl git tar xz sha256sum runuser; do command -v "$cmd" >/dev/null; done
stamp=$(date -u +%Y%m%dT%H%M%SZ)
record="/root/sysmonitor-install-$stamp"
install -d -m 700 "$record"
systemctl is-active x-ui > "$record/x-ui-before.txt" || true
ss -lntp > "$record/listeners-before.txt"
free -m > "$record/memory-before.txt"
df -h / > "$record/disk-before.txt"
nodever=v22.23.3
nodepath="/opt/sysmonitor-node-$nodever"
if [[ ! -d $nodepath ]]; then
  curl --fail --location --proto '=https' --tlsv1.2 "https://nodejs.org/dist/$nodever/SHASUMS256.txt" -o "$record/SHASUMS256.txt"
  archive="node-$nodever-linux-x64.tar.xz"
  curl --fail --location --proto '=https' --tlsv1.2 "https://nodejs.org/dist/$nodever/$archive" -o "$record/$archive"
  (cd "$record"; grep "  $archive\$" SHASUMS256.txt | sha256sum -c -)
  install -d "$nodepath"
  tar -xJf "$record/$archive" -C "$nodepath" --strip-components=1
fi
id syskuma >/dev/null 2>&1 || useradd --system --home-dir /var/lib/sysmonitor-kuma --shell /usr/sbin/nologin syskuma
install -d -o syskuma -g syskuma -m 700 /var/lib/sysmonitor-kuma
git clone --depth 1 --branch 2.5.6 https://github.com/louislam/uptime-kuma.git /opt/uptime-kuma
[[ $(git -C /opt/uptime-kuma rev-parse HEAD) == 7ca4a3debd76443d8452da95d4040200a1479af4 ]] || { echo 'Unexpected upstream commit'; exit 1; }
chown -R syskuma:syskuma /opt/uptime-kuma
runuser -u syskuma -- env PATH="$nodepath/bin:$PATH" HOME=/var/lib/sysmonitor-kuma NODE_OPTIONS=--max-old-space-size=384 bash -c 'cd /opt/uptime-kuma && nice -n 10 npm ci --omit=dev --no-audit && npm run download-dist' > "$record/npm-install.log" 2>&1
[[ -s /opt/uptime-kuma/dist/index.html ]] || { echo 'Upstream distribution missing'; exit 1; }
chown -R root:root /opt/uptime-kuma
cat > /etc/systemd/system/sysmonitor-kuma.service <<'UNIT'
[Unit]
Description=SysMonitor Uptime Kuma web console
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=syskuma
Group=syskuma
WorkingDirectory=/opt/uptime-kuma
Environment=NODE_ENV=production
Environment=UPTIME_KUMA_HOST=127.0.0.1
Environment=UPTIME_KUMA_PORT=3001
Environment=DATA_DIR=/var/lib/sysmonitor-kuma
Environment=NODE_OPTIONS=--max-old-space-size=384
ExecStart=/opt/sysmonitor-node-v22.23.3/bin/node /opt/uptime-kuma/server/server.js
Restart=on-failure
RestartSec=10
TimeoutStopSec=45
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/sysmonitor-kuma
RestrictSUIDSGID=true
CapabilityBoundingSet=
LockPersonality=true
MemoryMax=640M
CPUQuota=75%
TasksMax=128

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now sysmonitor-kuma
printf 'Install record: %s\n' "$record"
systemctl is-active x-ui sysmonitor-kuma
echo 'Kuma is bound to 127.0.0.1:3001. Complete first setup over an SSH tunnel.'
