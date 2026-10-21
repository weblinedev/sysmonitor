// One-time provisioning for pinned Kuma 2.5.6. Run as root; login token comes from stdin.
// This is not part of the long-running bridge and never edits Kuma's database directly.
const fs = require('fs');
const { io } = require('/opt/uptime-kuma/node_modules/socket.io-client');
const loginToken = fs.readFileSync(0, 'utf8').trim();
const env = Object.fromEntries(fs.readFileSync('/etc/sysmonitor/bridge.env', 'utf8').trim()
    .split('\n').map(line => { const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)]; }));
const socket = io('http://127.0.0.1:3001', {transports: ['websocket'], reconnection: false});
let notifications = [];
socket.on('notificationList', list => { notifications = list; });
const timer = setTimeout(() => { console.error('Provisioning timed out'); socket.disconnect(); process.exit(1); }, 30000);
const call = (event, ...args) => new Promise((resolve, reject) => {
    socket.timeout(10000).emit(event, ...args, (error, result) => error ? reject(error) : resolve(result));
});
socket.on('connect', async () => {
    try {
        if (!(await call('loginByToken', loginToken)).ok) throw Error('Login rejected');
        const existing = notifications.find(row => row.name === 'SysMonitor private bridge');
        const config = {name: 'SysMonitor private bridge', type: 'webhook', isDefault: false,
            active: true, httpMethod: 'post', webhookContentType: 'json',
            webhookURL: 'http://127.0.0.1:9081/kuma',
            webhookAdditionalHeaders: JSON.stringify({Authorization: 'Bearer ' + env.KUMA_WEBHOOK_TOKEN})};
        const result = await call('addNotification', config, existing ? existing.id : null);
        if (!result.ok) throw Error('Notification save rejected');
        for (const id of [1, 2, 3]) {
            const current = await call('getMonitor', id);
            if (!current.ok || !current.monitor.name.startsWith('Iran | ')) throw Error('Unexpected monitor; stop');
            current.monitor.notificationIDList = {...current.monitor.notificationIDList, [result.id]: true};
            if (!(await call('editMonitor', current.monitor)).ok) throw Error('Monitor update rejected');
        }
        if (!(await call('testNotification', config)).ok) throw Error('Notification test rejected');
        console.log('THREE_MONITORS_CONNECTED_AND_NOTIFICATION_ACCEPTED');
    } catch (error) {
        // Never print provider errors: an upstream exception can contain a credential-bearing URL.
        console.error('Provisioning failed; inspect the current state before retrying');
        process.exitCode = 1;
    } finally {
        clearTimeout(timer);
        socket.disconnect();
    }
});
