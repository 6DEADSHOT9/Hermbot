/* No API, session, transcript or artifact caching. Every push remains user visible. */
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
  let payload = {};
  try { const value = event.data?.json(); payload = value && typeof value === 'object' ? value : {}; } catch { /* Display generic text even for malformed data. */ }
  const target = typeof payload.target === 'string' && (/^[a-f0-9-]{36}$/.test(payload.target) || payload.target === 'inbox') ? payload.target : 'inbox';
  event.waitUntil(self.registration.showNotification('Hermbot', {
    body: 'Hermbot has an update. Open Hermbot to view the current status.',
    tag: typeof payload.tag === 'string' ? payload.tag.slice(0,32) : 'hermbot-update',
    renotify: false, data: {target}, icon: '/notification-icon.svg', badge: '/notification-icon.svg'
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = event.notification.data?.target;
  const url = new URL('/', self.location.origin);
  url.searchParams.set('attention', /^[a-f0-9-]{36}$/.test(target) ? target : 'inbox');
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({type:'window', includeUncontrolled:true});
    for (const client of windows) {
      if (new URL(client.url).origin === url.origin && 'focus' in client) {
        await client.navigate(url.href); return client.focus();
      }
    }
    return self.clients.openWindow(url.href);
  })());
});
