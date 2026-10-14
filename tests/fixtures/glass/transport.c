#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
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
    return original(fd, (const struct sockaddr *)&local, sizeof(local));
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
