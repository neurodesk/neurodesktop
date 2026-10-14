#define _GNU_SOURCE
#include <dlfcn.h>
#include <X11/Xlib.h>
#include <errno.h>
#include <poll.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <linux/vm_sockets.h>
#include <unistd.h>

int socket(int domain, int type, int protocol) {
    int (*original)(int, int, int) = dlsym(RTLD_NEXT, "socket");
    return original(domain == AF_VSOCK ? AF_UNIX : domain, type, protocol);
}

static int pending_fd = -1;

int connect(int fd, const struct sockaddr *address, socklen_t length) {
    int (*original)(int, const struct sockaddr *, socklen_t) = dlsym(RTLD_NEXT, "connect");
    if (address->sa_family != AF_VSOCK) return original(fd, address, length);
    const struct sockaddr_vm *vm = (const struct sockaddr_vm *)address;
    if (vm->svm_cid != VMADDR_CID_HOST || vm->svm_port != (unsigned)atoi(getenv("GLASS_TEST_PORT"))) {
        errno = EINVAL;
        return -1;
    }
    struct sockaddr_un local = {.sun_family = AF_UNIX};
    snprintf(local.sun_path, sizeof(local.sun_path), "%s", getenv("GLASS_TEST_SOCKET"));
    int result = original(fd, (const struct sockaddr *)&local, sizeof(local));
    const char *pending = getenv("GLASS_TEST_CONNECT_PENDING");
    if (result == 0 && pending && access(pending, F_OK) == 0) {
        pending_fd = fd;
        errno = EINPROGRESS;
        return -1;
    }
    return result;
}

int poll(struct pollfd *fds, nfds_t count, int timeout) {
    int (*original)(struct pollfd *, nfds_t, int) = dlsym(RTLD_NEXT, "poll");
    const char *pending = getenv("GLASS_TEST_CONNECT_PENDING");
    nfds_t masked = count;
    if (pending && access(pending, F_OK) == 0) {
        for (nfds_t i = 0; i < count; i++) {
            if (fds[i].fd == pending_fd && (fds[i].events & POLLOUT)) {
                masked = i;
                fds[i].events &= ~POLLOUT;
                break;
            }
        }
    }
    int result = original(fds, count, timeout);
    if (masked < count) fds[masked].events |= POLLOUT;
    return result;
}

int execl(const char *path, const char *arg, ...) {
    char *args[16];
    unsigned count = 0;
    va_list ap;
    va_start(ap, arg);
    args[count++] = (char *)arg;
    while (count < 15 && (args[count] = va_arg(ap, char *)) != NULL) count++;
    args[count] = NULL;
    va_end(ap);
    const char *recorder = getenv("GLASS_TEST_RECORDER");
    return execv(strcmp(path, "/usr/bin/xrandr") == 0 && recorder ? recorder : path, args);
}

Status XSendEvent(Display *display, Window window, Bool propagate, long mask, XEvent *event) {
    Status (*original)(Display *, Window, Bool, long, XEvent *) = dlsym(RTLD_NEXT, "XSendEvent");
    if (getenv("GLASS_TEST_UNRELATED_ERROR")) {
        int (*change)(Display *, Window, Atom, Atom, int, int, const unsigned char *, int) =
            dlsym(RTLD_NEXT, "XChangeProperty");
        change(display, window, None, None, 8, PropModeReplace, NULL, 0);
    }
    return original(display, window, propagate, mask, event);
}
