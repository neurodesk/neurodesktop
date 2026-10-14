#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <linux/vm_sockets.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

#define DISPLAY_PORT 10779

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

static int connect_host(void) {
    int fd = socket(AF_VSOCK, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    struct sockaddr_vm address;
    memset(&address, 0, sizeof(address));
    address.svm_family = AF_VSOCK;
    address.svm_cid = VMADDR_CID_HOST;
    address.svm_port = DISPLAY_PORT;
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static int apply_size(uint32_t width, uint32_t height) {
    char mode[32];
    if (snprintf(mode, sizeof(mode), "%ux%u", width, height) >= (int)sizeof(mode)) {
        return -1;
    }
    pid_t child = fork();
    if (child < 0) {
        return -1;
    }
    if (child == 0) {
        execl("/usr/bin/xrandr", "xrandr", "--screen", "0", "--size", mode, (char *)NULL);
        _exit(127);
    }
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) {
            return -1;
        }
    }
    return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : -1;
}

int main(void) {
    signal(SIGPIPE, SIG_IGN);
    for (;;) {
        int fd = connect_host();
        if (fd < 0) {
            usleep(250000);
            continue;
        }
        for (;;) {
            uint32_t raw[2];
            if (read_full(fd, raw, sizeof(raw)) != 0) {
                break;
            }
            uint32_t width = ntohl(raw[0]);
            uint32_t height = ntohl(raw[1]);
            if (width == 0 || height == 0 || width > 8192 || height > 8192) {
                break;
            }
            for (int attempt = 0; attempt < 40; attempt++) {
                if (apply_size(width, height) == 0) {
                    break;
                }
                usleep(50000);
            }
        }
        close(fd);
        usleep(250000);
    }
}
