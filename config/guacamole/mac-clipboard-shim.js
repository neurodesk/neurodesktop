/*
 * macOS clipboard shim for the Guacamole webapp: makes Cmd+V paste into the
 * remote desktop, in every browser.
 *
 * Without it, Cmd+V merely forwards Super+V to the remote session, which
 * pastes nothing. Per browser:
 *
 *   - Safari additionally has NO working local-to-remote clipboard sync at
 *     all: Guacamole's stock sync calls navigator.clipboard.readText() on
 *     window focus, and Safari has no persistable clipboard permission —
 *     reads outside an explicit paste gesture are rejected or demand a
 *     per-read "Paste" callout. This shim is the only paste path.
 *   - Firefox similarly restricts readText(), so the stock focus-driven sync
 *     does not work there either.
 *   - Chrome/Edge have a working focus-driven sync (after a one-time
 *     permission grant) but still need Ctrl+V, which VTE terminals swallow
 *     as quoted-insert; the shim gives them a real Cmd+V.
 *
 * A paste event's clipboardData is readable in all engines without any
 * permission. The shim therefore:
 *
 *   - Intercepts Cmd+V, lets the browser's paste command proceed into a
 *     hidden textarea, reads the text from the resulting paste event's
 *     clipboardData (instant and prompt-free), pushes it onto the remote
 *     clipboard through Guacamole's own clipboardService (which streams it
 *     over the tunnel), then synthesizes Shift+Insert in the remote session
 *     so a single Cmd+V "just pastes".
 *   - Caches remote-copy events (which Safari/Firefox block from being
 *     written to the local clipboard outside a gesture) and flushes them to
 *     the local clipboard on the next user gesture (Cmd+C or a mouse click).
 *
 * The script is injected into the Guacamole webapp's index.html at image
 * build time (see Dockerfile). It is a no-op on non-macOS platforms: only
 * Mac keyboards produce Cmd+V, and gating by platform keeps the Windows
 * key + V (OS clipboard history) untouched on other systems.
 */
(function macClipboardShim() {
    'use strict';

    var isMac = /Mac/.test(navigator.platform || '')
        || /Macintosh/.test(navigator.userAgent || '');
    if (!isMac)
        return;

    // X11 keysyms as used by the Guacamole protocol. Shift+Insert is the
    // paste keystroke: unlike Ctrl+V it also pastes in VTE terminals
    // (LXTerminal reads Ctrl+V as quoted-insert and prints ^V) while being
    // the standard paste binding in GTK/Qt apps, browsers, and Windows.
    var KEYSYM_SHIFT_L = 0xFFE1;
    var KEYSYM_INSERT = 0xFF63;
    var KEYSYM_SUPER_L = 0xFFEB;
    var KEYSYM_SUPER_R = 0xFFEC;

    /**
     * Delay in milliseconds between pushing clipboard data to the remote
     * session and synthesizing the paste keystroke. The clipboard stream and
     * key events travel over the same ordered tunnel, so this only needs to
     * cover the promise chain that broadcasts the clipboard change.
     */
    var REMOTE_PASTE_DELAY = 100;

    /**
     * How long to wait, in milliseconds, for the browser's paste command to
     * deliver a paste event to the hidden textarea before giving up (e.g.
     * when the clipboard holds no text flavor). Measured latency in Safari
     * is ~10ms.
     */
    var PASTE_EVENT_TIMEOUT = 500;

    /**
     * The most recent plain-text data seen on Guacamole's internal clipboard.
     */
    var latestText = null;

    /**
     * Whether latestText originated from the remote session and still needs
     * to be written to the local clipboard (Safari rejects the write that
     * Guacamole itself attempts because it happens outside a user gesture).
     */
    var pendingLocalWrite = false;

    /**
     * Returns whether the given element accepts text input.
     */
    var isEditable = function isEditable(element) {
        if (!element)
            return false;
        var tag = (element.tagName || '').toUpperCase();
        return tag === 'INPUT' || tag === 'TEXTAREA' || !!element.isContentEditable;
    };

    /**
     * Returns whether the given element is actually visible within the
     * viewport. Guacamole keeps keyboard focus in an offscreen input-sink
     * textarea whenever a session has focus, so "focus is in a textarea"
     * alone does not mean the user is editing text.
     */
    var isVisible = function isVisible(element) {
        if (!element.getBoundingClientRect)
            return false;
        var rect = element.getBoundingClientRect();
        return rect.width > 2 && rect.height > 2
            && rect.right > 0 && rect.bottom > 0
            && rect.left < (window.innerWidth || document.documentElement.clientWidth)
            && rect.top < (window.innerHeight || document.documentElement.clientHeight);
    };

    /**
     * Returns whether the given element is a real, user-visible text input
     * whose native clipboard behavior must be left untouched (the clipboard
     * textarea in Guacamole's own menu, or the login form) — as opposed to
     * Guacamole's offscreen input sink.
     */
    var isRealEditable = function isRealEditable(element) {
        return isEditable(element) && isVisible(element);
    };

    var install = function install(injector) {

        var $rootScope        = injector.get('$rootScope');
        var clipboardService  = injector.get('clipboardService');
        var ClipboardData     = injector.get('ClipboardData');
        var guacClientManager = injector.get('guacClientManager');

        // Offscreen textarea that receives the browser's paste command when
        // Cmd+V is intercepted; the resulting paste event exposes the
        // clipboard text without any permission prompt.
        var pasteTarget = document.createElement('textarea');
        pasteTarget.setAttribute('aria-hidden', 'true');
        pasteTarget.tabIndex = -1;
        pasteTarget.style.cssText =
            'position:absolute;left:-9999px;top:0;width:1px;height:1px;opacity:0;';
        document.body.appendChild(pasteTarget);

        /**
         * Returns all ManagedClients on this page that have an underlying
         * Guacamole.Client (Neurodesktop sessions have exactly one).
         */
        var getClients = function getClients() {
            var managed = guacClientManager.getManagedClients();
            var clients = [];
            for (var id in managed) {
                if (managed[id] && managed[id].client)
                    clients.push(managed[id]);
            }
            return clients;
        };

        /**
         * Sends a Shift+Insert press/release to every connected client,
         * pasting whatever was just streamed to the remote clipboard. The
         * user's physical Cmd key is typically still held down at this
         * point and Guacamole has already forwarded it as Super, so Super
         * is released first to keep the paste keystroke unmodified.
         */
        var sendRemotePaste = function sendRemotePaste() {
            getClients().forEach(function pasteInto(managedClient) {
                var client = managedClient.client;
                client.sendKeyEvent(0, KEYSYM_SUPER_L);
                client.sendKeyEvent(0, KEYSYM_SUPER_R);
                client.sendKeyEvent(1, KEYSYM_SHIFT_L);
                client.sendKeyEvent(1, KEYSYM_INSERT);
                client.sendKeyEvent(0, KEYSYM_INSERT);
                client.sendKeyEvent(0, KEYSYM_SHIFT_L);
            });
        };

        /**
         * Writes a pending remote copy to the local clipboard. Must be called
         * from within a user gesture: Safari requires transient activation
         * for clipboard writes.
         */
        var flushLocalClipboard = function flushLocalClipboard() {
            if (!pendingLocalWrite || latestText === null)
                return;
            if (!(navigator.clipboard && navigator.clipboard.writeText))
                return;
            navigator.clipboard.writeText(latestText).then(function written() {
                pendingLocalWrite = false;
            }, function ignore() {});
        };

        // Track Guacamole's internal clipboard. Data copied in the remote
        // session carries the originating client id as its source; that is
        // the data Safari failed to write locally and that we flush on the
        // next user gesture.
        $rootScope.$on('guacClipboard', function onClipboard(event, data) {
            if (data && data.type === 'text/plain' && typeof data.data === 'string') {
                latestText = data.data;
                if (data.source)
                    pendingLocalWrite = true;
            }
        });

        // Capture-phase listener on window fires before Guacamole's own
        // document-level keyboard handlers, so stopImmediatePropagation()
        // keeps intercepted combos out of the remote key stream.
        window.addEventListener('keydown', function onKeydown(e) {

            if (isRealEditable(e.target) || isRealEditable(document.activeElement))
                return;

            if (!e.metaKey || e.ctrlKey || e.altKey || e.shiftKey)
                return;

            var key = (e.key || '').toLowerCase();

            // Cmd+V: redirect the browser's paste command into the hidden
            // textarea, read the text from the paste event's clipboardData
            // (prompt-free, unlike navigator.clipboard.readText()), stream
            // it to the remote clipboard, then paste remotely.
            if (key === 'v') {

                if (!getClients().length)
                    return;

                // Keep the keystroke away from Guacamole's own handlers: they
                // must not forward Super+V to the remote session, and their
                // preventDefault() would cancel the very paste command this
                // relies on. The default action must proceed, so no
                // preventDefault() here.
                e.stopImmediatePropagation();

                var previousFocus = document.activeElement;
                var timeout = null;
                var onPaste = null;

                var cleanup = function cleanup() {
                    window.clearTimeout(timeout);
                    pasteTarget.removeEventListener('paste', onPaste);
                    pasteTarget.blur();
                    if (previousFocus && previousFocus !== pasteTarget
                            && previousFocus.focus)
                        previousFocus.focus();
                };

                onPaste = function onPaste(pasteEvent) {

                    var text = '';
                    try {
                        text = pasteEvent.clipboardData.getData('text/plain') || '';
                    }
                    catch (ignore) {}

                    // The data has been captured; nothing needs to be
                    // inserted into the textarea, and the event must not
                    // reach Guacamole's window-level clipboard listeners.
                    pasteEvent.preventDefault();
                    pasteEvent.stopPropagation();
                    cleanup();

                    if (!text)
                        return;

                    $rootScope.$apply(function updateClipboard() {
                        clipboardService.setClipboard(new ClipboardData({
                            type : 'text/plain',
                            data : text
                        }));
                    });
                    window.setTimeout(sendRemotePaste, REMOTE_PASTE_DELAY);

                };

                pasteTarget.addEventListener('paste', onPaste);
                pasteTarget.value = '';
                pasteTarget.focus();

                // If the paste command never arrives (non-text clipboard,
                // paste disabled), restore focus and give up quietly.
                timeout = window.setTimeout(cleanup, PASTE_EVENT_TIMEOUT);

            }

            // Cmd+C after a copy in the remote session: flush the cached
            // remote clipboard into the local clipboard.
            else if (key === 'c' && pendingLocalWrite) {
                e.preventDefault();
                e.stopImmediatePropagation();
                flushLocalClipboard();
            }

        }, true);

        // Any click is also a gesture usable to flush a pending remote copy,
        // making remote-to-local sync mostly transparent.
        window.addEventListener('mousedown', flushLocalClipboard, true);

    };

    // Wait for the AngularJS app to bootstrap before wiring into it.
    var attempts = 0;
    var poll = window.setInterval(function awaitInjector() {

        var injector = window.angular
            && window.angular.element(document.body).injector();

        if (injector) {
            window.clearInterval(poll);
            install(injector);
        }
        else if (++attempts >= 100)
            window.clearInterval(poll);

    }, 100);

})();
