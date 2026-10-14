#define _GNU_SOURCE

#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <arpa/inet.h>
#include <errno.h>
#include <linux/vm_sockets.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
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
    int fd = socket(AF_VSOCK, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    struct sockaddr_vm address;
    memset(&address, 0, sizeof(address));
    address.svm_family = AF_VSOCK;
    address.svm_cid = VMADDR_CID_HOST;
    address.svm_port = CLIPBOARD_PORT;
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static void answer_selection(
    Display *display,
    XSelectionRequestEvent *request,
    Atom targets,
    Atom utf8,
    const unsigned char *text,
    size_t length) {
    XSelectionEvent response;
    memset(&response, 0, sizeof(response));
    response.type = SelectionNotify;
    response.display = request->display;
    response.requestor = request->requestor;
    response.selection = request->selection;
    response.target = request->target;
    response.time = request->time;
    response.property = None;

    Atom property = request->property == None ? request->target : request->property;
    if (request->target == targets) {
        Atom supported[] = {targets, utf8, XA_STRING};
        XChangeProperty(
            display, request->requestor, property, XA_ATOM, 32,
            PropModeReplace, (unsigned char *)supported,
            (int)(sizeof(supported) / sizeof(supported[0])));
        response.property = property;
    } else if (request->target == utf8 || request->target == XA_STRING) {
        XChangeProperty(
            display, request->requestor, property, request->target, 8,
            PropModeReplace, text, (int)length);
        response.property = property;
    }
    XSendEvent(display, request->requestor, False, 0, (XEvent *)&response);
    XFlush(display);
}

static int run_bridge(Display *display, Window window, Atom clipboard, Atom targets, Atom utf8, Atom property, int fd) {
    unsigned char *owned = NULL;
    size_t owned_length = 0;
    unsigned char *last_sent = NULL;
    size_t last_sent_length = 0;
    Window observed_owner = None;
    int xfd = ConnectionNumber(display);

    for (;;) {
        struct pollfd pollfds[] = {
            {.fd = xfd, .events = POLLIN},
            {.fd = fd, .events = POLLIN},
        };
        int ready = poll(pollfds, 2, 200);
        if (ready < 0 && errno != EINTR) {
            break;
        }
        if (pollfds[1].revents & (POLLERR | POLLHUP | POLLNVAL)) {
            break;
        }
        if (pollfds[1].revents & POLLIN) {
            uint32_t encoded = 0;
            if (read_full(fd, &encoded, sizeof(encoded)) != 0) {
                break;
            }
            uint32_t length = ntohl(encoded);
            if (length > CLIPBOARD_MAX) {
                break;
            }
            unsigned char *next = malloc((size_t)length + 1);
            if (next == NULL || read_full(fd, next, length) != 0) {
                free(next);
                break;
            }
            next[length] = '\0';
            free(owned);
            owned = next;
            owned_length = length;
            XSetSelectionOwner(display, clipboard, window, CurrentTime);
            observed_owner = window;
            XFlush(display);
        }

        while (XPending(display) != 0) {
            XEvent event;
            XNextEvent(display, &event);
            if (event.type == SelectionRequest) {
                answer_selection(display, &event.xselectionrequest, targets, utf8, owned, owned_length);
            } else if (event.type == SelectionClear) {
                observed_owner = None;
            } else if (event.type == SelectionNotify && event.xselection.property != None) {
                Atom actual_type;
                int actual_format;
                unsigned long items;
                unsigned long remaining;
                unsigned char *value = NULL;
                if (XGetWindowProperty(
                        display, window, property, 0, CLIPBOARD_MAX / 4,
                        True, AnyPropertyType, &actual_type, &actual_format,
                        &items, &remaining, &value) == Success &&
                    actual_format == 8 && remaining == 0 && items <= CLIPBOARD_MAX &&
                    (items != last_sent_length ||
                     (items != 0 && memcmp(value, last_sent, items) != 0))) {
                    if (send_text(fd, value, items) != 0) {
                        if (value != NULL) {
                            XFree(value);
                        }
                        free(owned);
                        free(last_sent);
                        return -1;
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
        if (owner != None && owner != window && owner != observed_owner) {
            observed_owner = owner;
            XConvertSelection(display, clipboard, utf8, property, window, CurrentTime);
            XFlush(display);
        }
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
    Atom clipboard = XInternAtom(display, "CLIPBOARD", False);
    Atom targets = XInternAtom(display, "TARGETS", False);
    Atom utf8 = XInternAtom(display, "UTF8_STRING", False);
    Atom property = XInternAtom(display, "GLASS_CLIPBOARD", False);

    for (;;) {
        int fd = connect_host();
        if (fd < 0) {
            usleep(250000);
            continue;
        }
        run_bridge(display, window, clipboard, targets, utf8, property, fd);
        close(fd);
        usleep(250000);
    }
}
