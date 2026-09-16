/* Runs only in the T3 iframe, before its modules capture fetch/WebSocket.
 * T3's same-origin primary environment uses its own session cookie. Remote
 * environments retain their original URLs and authorization headers.
 */
(() => {
  const prefix = __T3_PREFIX__;
  function target(value) {
    const url = new URL(value, location.href);
    const protocol = url.protocol.replace(/^ws/, 'http');
    const local = url.host === location.host && protocol === location.protocol;
    if (local && !url.pathname.startsWith(prefix)) {
      url.pathname = prefix + url.pathname.replace(/^\//, '');
    }
    return { url: url.href, local };
  }
  window.__neurodeskT3Target = target;
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const mapped = target(input instanceof Request ? input.url : input);
    if (!mapped.local) return originalFetch(input, init);
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    const cookie = document.cookie.split('; ').find(value => value.startsWith('_xsrf='));
    if (cookie) headers.set('X-XSRFToken', decodeURIComponent(cookie.slice(6)));
    if (!(input instanceof Request)) {
      return originalFetch(mapped.url, { ...init, headers });
    }
    // Passing a Request as RequestInit exposes its ReadableStream body. Firefox
    // can stringify that stream rather than upload it. Materialize only this
    // branch; ordinary fetch(url, init) keeps its original Blob/FormData/body.
    const request = new Request(input, init);
    return originalFetch(mapped.url, {
      method: request.method, headers,
      body: request.body === null ? undefined : await request.arrayBuffer(),
      credentials: request.credentials, mode: request.mode, cache: request.cache,
      redirect: request.redirect, referrer: request.referrer,
      referrerPolicy: request.referrerPolicy, integrity: request.integrity,
      keepalive: request.keepalive, signal: request.signal
    });
  };
  const OriginalWebSocket = window.WebSocket;
  window.WebSocket = class extends OriginalWebSocket {
    constructor(url, protocols) {
      super(target(url).url, protocols);
    }
  };
})();
