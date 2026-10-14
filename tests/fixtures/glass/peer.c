#define _GNU_SOURCE
#include <X11/Xlib.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    Display *d = XOpenDisplay(NULL);
    if (!d) return 2;
    setbuf(stdout, NULL);
    setbuf(stdin, NULL);
    Window w = XCreateSimpleWindow(d, DefaultRootWindow(d), 0, 0, 1, 1, 0, 0, 0);
    Atom clipboard = XInternAtom(d, "CLIPBOARD", False);
    Atom utf8 = XInternAtom(d, "UTF8_STRING", False);
    Atom property = XInternAtom(d, "PEER_TEXT", False);
    char text[1024] = "";
    int mode = 0;
    XEvent held = {0};
    puts("ready");
    for (;;) {
        struct pollfd fds[] = {{0, POLLIN, 0}, {ConnectionNumber(d), POLLIN, 0}};
        poll(fds, 2, 20);
        if (fds[0].revents & POLLIN) {
            char line[1100];
            if (!fgets(line, sizeof(line), stdin)) break;
            line[strcspn(line, "\n")] = 0;
            if (!strncmp(line, "own ", 4) || !strncmp(line, "set ", 4)) {
                snprintf(text, sizeof(text), "%s", line + 4);
                if (line[0] == 'o') XSetSelectionOwner(d, clipboard, w, CurrentTime);
                mode = 0;
            } else if (!strcmp(line, "refuse")) mode = 1;
            else if (!strcmp(line, "hold")) mode = 2;
            else if (!strcmp(line, "release")) {
                if (held.type) XPutBackEvent(d, &held);
                mode = 0;
            } else if (!strcmp(line, "paste") || !strcmp(line, "destroy")) {
                Window reader = XCreateSimpleWindow(d, DefaultRootWindow(d), 0, 0, 1, 1, 0, 0, 0);
                XConvertSelection(d, clipboard, utf8, property, reader, CurrentTime);
                if (line[0] == 'd') XDestroyWindow(d, reader);
            }
            XSync(d, False);
            puts("ok");
        }
        while (XPending(d)) {
            XEvent e;
            XNextEvent(d, &e);
            if (e.type == SelectionRequest) {
                XSelectionRequestEvent *r = &e.xselectionrequest;
                if (mode == 2) { held = e; continue; }
                XEvent reply = {0};
                reply.xselection = (XSelectionEvent){.type = SelectionNotify, .display = d,
                    .requestor = r->requestor, .selection = r->selection, .target = r->target,
                    .property = mode == 1 ? None : r->property, .time = r->time};
                if (!mode) XChangeProperty(d, r->requestor, r->property, utf8, 8,
                    PropModeReplace, (unsigned char *)text, (int)strlen(text));
                XSendEvent(d, r->requestor, False, 0, &reply);
                XFlush(d);
            } else if (e.type == SelectionNotify) {
                Atom type;
                int format;
                unsigned long n, rest;
                unsigned char *value = NULL;
                XGetWindowProperty(d, e.xselection.requestor, property, 0, 1024, True,
                    AnyPropertyType, &type, &format, &n, &rest, &value);
                printf("text:%.*s\n", (int)n, value ? (char *)value : "");
                if (value) XFree(value);
                XDestroyWindow(d, e.xselection.requestor);
            }
        }
    }
    XCloseDisplay(d);
    return 0;
}
