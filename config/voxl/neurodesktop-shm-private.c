/*
 * Make an X server's SysV shared-memory framebuffer private to its owner.
 *
 * Xvfb -shmem creates its framebuffer segment with mode 0777. On hosts where
 * users share an IPC namespace (Apptainer on HPC nodes), anyone could attach
 * it and read or write the desktop. This sets every segment the given process
 * created, and the caller owns, to mode 0600. It then requires that process
 * to be the segment's only attachment: a peer that attached before the mode
 * change would keep its mapping, so the framebuffer must be discarded.
 *
 * Usage: neurodesktop-shm-private PID
 * Exit 0: at least one segment found, all private and unshared.
 * Exit 1: usage error or no segment found. Exit 2: a segment could not be
 * made private or was already attached by another process.
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s PID\n", argv[0]);
        return 1;
    }
    char *end = NULL;
    long pid = strtol(argv[1], &end, 10);
    if (end == argv[1] || *end != '\0' || pid <= 0) {
        fprintf(stderr, "invalid PID: %s\n", argv[1]);
        return 1;
    }

    FILE *table = fopen("/proc/sysvipc/shm", "r");
    if (table == NULL) {
        perror("/proc/sysvipc/shm");
        return 1;
    }
    char line[512];
    if (fgets(line, sizeof line, table) == NULL) {
        fclose(table);
        return 1;
    }

    uid_t uid = getuid();
    int found = 0;
    int status = 0;
    while (fgets(line, sizeof line, table) != NULL) {
        long key, shmid, perms, cpid, lpid, nattch, owner;
        unsigned long size;
        /* key shmid perms size cpid lpid nattch uid ... */
        if (sscanf(line, "%ld %ld %lo %lu %ld %ld %ld %ld", &key, &shmid, &perms, &size,
                   &cpid, &lpid, &nattch, &owner) != 8) {
            continue;
        }
        if (cpid != pid || (uid_t)owner != uid) {
            continue;
        }
        found = 1;
        struct shmid_ds ds;
        if (shmctl((int)shmid, IPC_STAT, &ds) != 0) {
            fprintf(stderr, "shmctl IPC_STAT %ld: %s\n", shmid, strerror(errno));
            status = 2;
            continue;
        }
        ds.shm_perm.mode = (ds.shm_perm.mode & ~0777) | 0600;
        if (shmctl((int)shmid, IPC_SET, &ds) != 0 || shmctl((int)shmid, IPC_STAT, &ds) != 0) {
            fprintf(stderr, "shmctl IPC_SET %ld: %s\n", shmid, strerror(errno));
            status = 2;
            continue;
        }
        if ((ds.shm_perm.mode & 0777) != 0600 || ds.shm_nattch != 1) {
            fprintf(stderr, "segment %ld has mode %o and %lu attachments; refusing it\n", shmid,
                    (unsigned)(ds.shm_perm.mode & 0777), (unsigned long)ds.shm_nattch);
            status = 2;
        }
    }
    fclose(table);
    if (!found) {
        fprintf(stderr, "no shared-memory segment created by PID %ld\n", pid);
        return 1;
    }
    return status;
}
