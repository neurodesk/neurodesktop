#define _GNU_SOURCE

#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <X11/Xproto.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/vm_sockets.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define CLIPBOARD_PORT 10778
#define CLIPBOARD_MAX (64U << 20)

static int read_full(int fd, void *buffer, size_t length) {
    unsigned char *cursor = buffer;
    while (length != 0) {
        ssize_t count = read(fd, cursor, length);
        if (count == 0) {
            return -1;
        }
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        cursor += count;
        length -= (size_t)count;
    }
    return 0;
}

static int write_full(int fd, const void *buffer, size_t length) {
    const unsigned char *cursor = buffer;
    while (length != 0) {
        ssize_t count = write(fd, cursor, length);
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        cursor += count;
        length -= (size_t)count;
    }
    return 0;
}

static int send_text(int fd, const unsigned char *text, size_t length) {
    if (length > CLIPBOARD_MAX) {
        return -1;
    }
    uint32_t encoded = htonl((uint32_t)length);
    return write_full(fd, &encoded, sizeof(encoded)) ||
        write_full(fd, text, length);
}

static int connect_host(void) {
    int fd = socket(AF_VSOCK, SOCK_STREAM | SOCK_NONBLOCK, 0);
    if (fd < 0) {
        return -1;
    }
    struct sockaddr_vm address;
    memset(&address, 0, sizeof(address));
    address.svm_family = AF_VSOCK;
    address.svm_cid = VMADDR_CID_HOST;
    address.svm_port = CLIPBOARD_PORT;
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) != 0) {
        if (errno != EINPROGRESS) {
            close(fd);
            return -1;
        }
        struct pollfd pending = {.fd = fd, .events = POLLOUT};
        int error = 0;
        socklen_t error_length = sizeof(error);
        // Bound reconnect work so an unavailable relay cannot stall X requests.
        if (poll(&pending, 1, 100) <= 0 ||
            getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &error_length) != 0 || error != 0) {
            close(fd);
            return -1;
        }
    }
    int flags = fcntl(fd, F_GETFL);
    if (flags < 0 || fcntl(fd, F_SETFL, flags & ~O_NONBLOCK) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static struct {
    Display *display;
    Window window;
    unsigned long first;
    unsigned long last;
    XErrorHandler previous;
} reply_errors;

static int reply_error(Display *display, XErrorEvent *error) {
    if (display == reply_errors.display && error->error_code == BadWindow &&
        error->resourceid == reply_errors.window &&
        error->serial >= reply_errors.first && error->serial <= reply_errors.last &&
        (error->request_code == X_ChangeProperty || error->request_code == X_SendEvent)) {
        return 0;
    }
    return reply_errors.previous(display, error);
}

static int64_t monotonic_ms(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static void answer_selection(
    Display *display,
    XSelectionRequestEvent *request,
    Atom targets,
    Atom utf8,
    const unsigned char *text,
    size_t length) {
    size_t request_words = (size_t)XMaxRequestSize(display);
    long extended_words = XExtendedMaxRequestSize(display);
    // BIG-REQUESTS adds one length word to the ordinary request header.
    if (extended_words > 0 && (size_t)extended_words - 1 > request_words) {
        request_words = (size_t)extended_words - 1;
    }
    size_t payload_words = request_words - sz_xChangePropertyReq / 4;

    XSelectionEvent response;
    memset(&response, 0, sizeof(response));
    response.type = SelectionNotify;
    response.display = request->display;
    response.requestor = request->requestor; // codespell:ignore requestor
    response.selection = request->selection;
    response.target = request->target;
    response.time = request->time;
    response.property = None;

    XSync(display, False);
    reply_errors.display = display;
    reply_errors.window = request->requestor; // codespell:ignore requestor
    reply_errors.first = NextRequest(display);
    reply_errors.last = reply_errors.first + 1;
    reply_errors.previous = XSetErrorHandler(reply_error);

    Atom property = request->property == None ? request->target : request->property;
    if (request->target == targets) {
        Atom supported[] = {targets, utf8, XA_STRING};
        XChangeProperty(
            display, request->requestor, property, XA_ATOM, 32, // codespell:ignore requestor
            PropModeReplace, (unsigned char *)supported,
            (int)(sizeof(supported) / sizeof(supported[0])));
        response.property = property;
    } else if ((request->target == utf8 || request->target == XA_STRING) &&
               (length + 3) / 4 <= payload_words) {
        XChangeProperty(
            display, request->requestor, property, request->target, 8, // codespell:ignore requestor
            PropModeReplace, text, (int)length);
        response.property = property;
    }
    XSendEvent(display, request->requestor, False, 0, (XEvent *)&response); // codespell:ignore requestor
    reply_errors.last = NextRequest(display) - 1;
    XSync(display, False);
    XSetErrorHandler(reply_errors.previous);
}

static int run_bridge(Display *display, Window window, Atom clipboard, Atom targets, Atom utf8, Atom property) {
    int fd = -1;
    int64_t next_connect = 0;
    unsigned char *owned = NULL;
    size_t owned_length = 0;
    unsigned char *last_sent = NULL;
    size_t last_sent_length = 0;
    Window pending_owner = None;
    Time pending_time = CurrentTime;
    int64_t deadline = 0;
    int64_t next_sample = 0;
    Atom clock_property = XInternAtom(display, "GLASS_SAMPLE_TIME", False);
    int xfd = ConnectionNumber(display);

    for (;;) {
        if (fd < 0 && monotonic_ms() >= next_connect) {
            fd = connect_host();
            next_connect = monotonic_ms() + 250;
        }
        struct pollfd pollfds[] = {
            {.fd = xfd, .events = POLLIN},
            {.fd = fd, .events = POLLIN},
        };
        int ready = poll(pollfds, 2, 200);
        if (ready < 0 && errno != EINTR) {
            break;
        }
        if (pollfds[1].revents & (POLLERR | POLLHUP | POLLNVAL)) {
            goto disconnected;
        }
        if (pollfds[1].revents & POLLIN) {
            uint32_t encoded = 0;
            if (read_full(fd, &encoded, sizeof(encoded)) != 0) {
                goto disconnected;
            }
            uint32_t length = ntohl(encoded);
            if (length > CLIPBOARD_MAX) {
                goto disconnected;
            }
            unsigned char *next = malloc((size_t)length + 1);
            if (next == NULL || read_full(fd, next, length) != 0) {
                free(next);
                goto disconnected;
            }
            next[length] = '\0';
            free(owned);
            owned = next;
            owned_length = length;
            XSetSelectionOwner(display, clipboard, window, CurrentTime);
            pending_owner = None;
            free(last_sent);
            last_sent = NULL;
            last_sent_length = 0;
            XFlush(display);
        }

        while (XPending(display) != 0) {
            XEvent event;
            XNextEvent(display, &event);
            if (event.type == SelectionRequest) {
                answer_selection(display, &event.xselectionrequest, targets, utf8, owned, owned_length);
            } else if (event.type == SelectionNotify && pending_owner != None &&
                       event.xselection.requestor == window && // codespell:ignore requestor
                       event.xselection.selection == clipboard &&
                       event.xselection.target == utf8 && event.xselection.time == pending_time &&
                       (event.xselection.property == property || event.xselection.property == None)) {
                Window sampled_owner = pending_owner;
                pending_owner = None;
                if (event.xselection.property == None ||
                    XGetSelectionOwner(display, clipboard) != sampled_owner) {
                    continue;
                }
                Atom actual_type;
                int actual_format;
                unsigned long items;
                unsigned long remaining;
                unsigned char *value = NULL;
                if (XGetWindowProperty(
                        display, window, property, 0, CLIPBOARD_MAX / 4,
                        True, AnyPropertyType, &actual_type, &actual_format,
                        &items, &remaining, &value) == Success &&
                    actual_type == utf8 && actual_format == 8 && remaining == 0 && items <= CLIPBOARD_MAX &&
                    (last_sent == NULL || items != last_sent_length ||
                     (items != 0 && memcmp(value, last_sent, items) != 0))) {
                    if (send_text(fd, value, items) != 0) {
                        if (value != NULL) {
                            XFree(value);
                        }
                        goto disconnected;
                    }
                    unsigned char *copy = malloc(items == 0 ? 1 : items);
                    if (copy != NULL) {
                        if (items != 0) {
                            memcpy(copy, value, items);
                        }
                        free(last_sent);
                        last_sent = copy;
                        last_sent_length = items;
                    }
                }
                if (value != NULL) {
                    XFree(value);
                }
            }
        }

        Window owner = XGetSelectionOwner(display, clipboard);
        int64_t now = monotonic_ms();
        if (pending_owner != None && (now >= deadline || owner != pending_owner)) {
            pending_owner = None;
        }
        if (fd >= 0 && owner != None && owner != window && pending_owner == None && now >= next_sample) {
            // A server timestamp distinguishes late replies from a timed-out sample.
            XChangeProperty(display, window, clock_property, utf8, 8, PropModeReplace, NULL, 0);
            XEvent timestamp;
            do {
                XWindowEvent(display, window, PropertyChangeMask, &timestamp);
            } while (timestamp.xproperty.atom != clock_property);
            pending_time = timestamp.xproperty.time;
            pending_owner = owner;
            deadline = now + 1000;
            next_sample = now + 200;
            XConvertSelection(display, clipboard, utf8, property, window, pending_time);
            XFlush(display);
        }
        continue;

    disconnected:
        close(fd);
        fd = -1;
        next_connect = monotonic_ms() + 250;
        pending_owner = None;
        next_sample = 0;
        free(last_sent);
        last_sent = NULL;
        last_sent_length = 0;
    }
    if (fd >= 0) {
        close(fd);
    }
    free(owned);
    free(last_sent);
    return -1;
}

int main(void) {
    signal(SIGPIPE, SIG_IGN);
    Display *display = NULL;
    while (display == NULL) {
        display = XOpenDisplay(NULL);
        if (display == NULL) {
            usleep(100000);
        }
    }
    Window window = XCreateSimpleWindow(
        display, DefaultRootWindow(display), -1, -1, 1, 1, 0, 0, 0);
    XSelectInput(display, window, PropertyChangeMask);
    Atom clipboard = XInternAtom(display, "CLIPBOARD", False);
    Atom targets = XInternAtom(display, "TARGETS", False);
    Atom utf8 = XInternAtom(display, "UTF8_STRING", False);
    Atom property = XInternAtom(display, "GLASS_CLIPBOARD", False);

    run_bridge(display, window, clipboard, targets, utf8, property);
    XCloseDisplay(display);
    return 1;
}
