/* server.c
   Servidor TCP en C con hilos (Berkeley sockets + pthreads).
   Compilar: gcc -pthread server.c -o server -lcrypto
   Ejecutar: ./server <port> <LogsFile>
*/

#define _POSIX_C_SOURCE 200809L
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#endif

#ifdef _WIN32
#ifndef SHUT_RDWR
#define SHUT_RDWR SD_BOTH
#endif
#endif
#include <errno.h>
#include <fcntl.h>
#ifndef _WIN32
#include <netinet/in.h>
#endif
#include <openssl/sha.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <sys/socket.h>
#endif
#include <time.h>
#include <unistd.h>

#define MAXLINE 2048
#define TOKEN_LEN 33
#define MAX_CLIENTS 256

typedef enum { ROLE_NONE=0, ROLE_OBSERVER, ROLE_ADMIN } role_t;

typedef struct client {
    int fd;
    struct sockaddr_in addr;
    role_t role;
    bool authenticated;
    char token[TOKEN_LEN];
    pthread_t thread;
    struct client *next;
} client_t;

typedef struct {
    char username[64];
    char salt[64];
    char hashhex[SHA256_DIGEST_LENGTH*2+1]; // hex of SHA256(salt+pass)
} cred_t;

/* Vehicle state */
typedef struct {
    double speed; // m/s
    int battery; // percent 0-100
    int direction_deg; // 0=N,90=E,180=S,270=W
    pthread_mutex_t lock;
} vehicle_t;

static client_t *clients = NULL;
static pthread_mutex_t clients_lock = PTHREAD_MUTEX_INITIALIZER;
static FILE *logf = NULL;
static int listen_fd = -1;
static cred_t admin_cred;
static bool cred_present = false;
static vehicle_t vehicle;
static char *logs_path = NULL;

/* UTIL: ISO8601 timestamp */
static void now_iso8601(char *buf, size_t n) {
    time_t t = time(NULL);
    struct tm tm;
    gmtime_r(&t, &tm);
    strftime(buf, n, "%Y-%m-%dT%H:%M:%SZ", &tm);
}

/* Logging (console + file) */
static void log_msg(const char *fmt, ...) {
    char ts[64];
    now_iso8601(ts, sizeof(ts));
    va_list ap;
    va_start(ap, fmt);
    fprintf(stdout, "[%s] ", ts);
    vfprintf(stdout, fmt, ap);
    fprintf(stdout, "\n");
    va_end(ap);

    if (logf) {
        va_start(ap, fmt);
        fprintf(logf, "[%s] ", ts);
        vfprintf(logf, fmt, ap);
        fprintf(logf, "\n");
        fflush(logf);
        va_end(ap);
    }
}

/* read credentials file ./credentials.txt format:
   username:salt:hexsha256(salt+password)
*/
static bool load_credentials(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return false;
    char line[512];
    if (!fgets(line, sizeof(line), f)) { fclose(f); return false; }
    fclose(f);
    // strip newline
    line[strcspn(line,"\r\n")] = 0;
    char *u = strtok(line, ":");
    char *salt = strtok(NULL, ":");
    char *hex = strtok(NULL, ":");
    if (!u || !salt || !hex) return false;
    strncpy(admin_cred.username, u, sizeof(admin_cred.username)-1);
    strncpy(admin_cred.salt, salt, sizeof(admin_cred.salt)-1);
    strncpy(admin_cred.hashhex, hex, sizeof(admin_cred.hashhex)-1);
    cred_present = true;
    return true;
}

/* compute sha256 hex of salt+password */
static void sha256_hex_of(const char *salt, const char *password, char *outhex) {
    unsigned char digest[SHA256_DIGEST_LENGTH];
    size_t a = strlen(salt), b = strlen(password);
    unsigned char *buf = malloc(a + b);
    memcpy(buf, salt, a);
    memcpy(buf + a, password, b);
    SHA256(buf, a + b, digest);
    free(buf);
    for (int i=0;i<SHA256_DIGEST_LENGTH;i++) sprintf(outhex + i*2, "%02x", digest[i]);
    outhex[SHA256_DIGEST_LENGTH*2] = 0;
}

/* generate random token hex length 32 (plus null) */
static void gen_token(char *out) {
    unsigned char r[16];
    int fd = open("/dev/urandom", O_RDONLY);
    if (fd < 0) {
        // fallback
        srand(time(NULL));
        for (int i=0;i<16;i++) r[i] = rand() & 0xFF;
    } else {
        read(fd, r, sizeof(r));
        close(fd);
    }
    for (int i=0;i<16;i++) sprintf(out + i*2, "%02x", r[i]);
    out[32] = 0;
}

/* client list management */
static void add_client(client_t *c) {
    pthread_mutex_lock(&clients_lock);
    c->next = clients;
    clients = c;
    pthread_mutex_unlock(&clients_lock);
}

static void remove_client(client_t *c) {
    pthread_mutex_lock(&clients_lock);
    client_t **p = &clients;
    while (*p) {
        if (*p == c) {
            *p = c->next;
            break;
        }
        p = &(*p)->next;
    }
    pthread_mutex_unlock(&clients_lock);
}

/* send text line (append \n) */
static bool send_line(client_t *c, const char *fmt, ...) {
    char buf[MAXLINE];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf)-2, fmt, ap);
    va_end(ap);
    strcat(buf, "\n");
    ssize_t n = send(c->fd, buf, strlen(buf), 0);
    if (n <= 0) return false;
    // log message
    char ip[INET_ADDRSTRLEN];
    inet_ntop(AF_INET, &c->addr.sin_addr, ip, sizeof(ip));
    int port = ntohs(c->addr.sin_port);
    log_msg("-> %s:%d  %s", ip, port, buf);
    return true;
}

/* get client identifier ip:port */
static void client_idstr(client_t *c, char *out, size_t n) {
    char ip[INET_ADDRSTRLEN];
    inet_ntop(AF_INET, &c->addr.sin_addr, ip, sizeof(ip));
    int port = ntohs(c->addr.sin_port);
    snprintf(out, n, "%s:%d", ip, port);
}

/* find client by token */
static client_t* find_client_by_token(const char *token) {
    pthread_mutex_lock(&clients_lock);
    client_t *it = clients;
    while (it) {
        if (it->authenticated && strcmp(it->token, token) == 0) {
            pthread_mutex_unlock(&clients_lock);
            return it;
        }
        it = it->next;
    }
    pthread_mutex_unlock(&clients_lock);
    return NULL;
}

/* list users string */
static void build_users_list(char *out, size_t n) {
    char tmp[4096] = {0};
    pthread_mutex_lock(&clients_lock);
    client_t *it = clients;
    int count = 0;
    while (it) {
        char id[64];
        client_idstr(it, id, sizeof(id));
        char *role = (it->role==ROLE_ADMIN) ? "ADMIN" : (it->role==ROLE_OBSERVER) ? "OBSERVER" : "NONE";
        char line[128];
        snprintf(line, sizeof(line), "%s:%s:%s\n", id, it->authenticated ? "AUTH" : "NOAUTH", role);
        strncat(tmp, line, sizeof(tmp)-strlen(tmp)-1);
        count++;
        it = it->next;
    }
    pthread_mutex_unlock(&clients_lock);
    snprintf(out, n, "USERS %d\n%s", count, tmp);
}

/* vehicle helpers */
static const char* dir_of_deg(int deg) {
    deg %= 360;
    if (deg < 0) deg += 360;
    if (deg < 45 || deg >= 315) return "N";
    if (deg < 135) return "E";
    if (deg < 225) return "S";
    return "W";
}

/* Broadcast telemetry to all */
static void broadcast_telemetry() {
    char ts[64], msg[MAXLINE];
    pthread_mutex_lock(&vehicle.lock);
    double v = vehicle.speed;
    int b = vehicle.battery;
    int deg = vehicle.direction_deg;
    pthread_mutex_unlock(&vehicle.lock);
    now_iso8601(ts, sizeof(ts));
    snprintf(msg, sizeof(msg), "TELEMETRY v=%.2f battery=%d dir=%s timestamp=%s", v, b, dir_of_deg(deg), ts);

    pthread_mutex_lock(&clients_lock);
    client_t *it = clients;
    client_t *prev = NULL;
    while (it) {
        // only send to authenticated subscribers (both roles get telemetry)
        if (it->authenticated) {
            ssize_t n = send(it->fd, msg, strlen(msg), 0);
            if (n <= 0) {
                // mark removal
                client_t *to_remove = it;
                it = it->next;
                if (prev) prev->next = it; else clients = it;
                char id[64]; client_idstr(to_remove, id, sizeof(id));
                log_msg("Client disconnected during broadcast: %s", id);
                close(to_remove->fd);
                free(to_remove);
                continue;
            } else {
                // send newline and log
                send(it->fd, "\n", 1, 0);
                char id[64]; client_idstr(it, id, sizeof(id));
                log_msg("-> %s  %s", id, msg);
            }
        }
        prev = it;
        it = it->next;
    }
    pthread_mutex_unlock(&clients_lock);
}

/* background broadcaster thread */
static void *broadcaster_thread(void *arg) {
    (void)arg;
    while (1) {
        sleep(10);
        // update battery slightly
        pthread_mutex_lock(&vehicle.lock);
        if (vehicle.speed > 0) vehicle.battery -= 1; // consume
        else if (vehicle.battery > 0 && vehicle.speed == 0) vehicle.battery -= 0; // idle
        if (vehicle.battery < 0) vehicle.battery = 0;
        pthread_mutex_unlock(&vehicle.lock);

        broadcast_telemetry();
    }
    return NULL;
}

/* process command from ADMIN */
static void process_cmd(client_t *c, const char *cmdline) {
    // cmdline example: "CMD SPEED UP"
    char copy[MAXLINE];
    strncpy(copy, cmdline, sizeof(copy)-1); copy[sizeof(copy)-1]=0;
    char *tok = strtok(copy, " \t\r\n");
    if (!tok) { send_line(c, "ERR invalid"); return; }
    if (strcmp(tok, "CMD") != 0) { send_line(c, "ERR expected CMD"); return; }
    char *rest = strtok(NULL, "\r\n");
    if (!rest) { send_line(c, "ERR missing action"); return; }
    // Normalize action: replace spaces by underscore and uppercase
    char action[64] = {0};
    int j=0;
    for (int i=0; rest[i] && j < (int)sizeof(action)-1; i++) {
        char ch = rest[i];
        if (ch==' ') action[j++] = '_';
        else action[j++] = ch;
    }
    action[j]=0;
    // uppercase
    for (int i=0;action[i];i++) if ('a'<=action[i] && action[i]<='z') action[i]-=32;

    pthread_mutex_lock(&vehicle.lock);
    int battery = vehicle.battery;
    double speed = vehicle.speed;
    pthread_mutex_unlock(&vehicle.lock);

    // check authorization
    if (!c->authenticated || c->role != ROLE_ADMIN) {
        send_line(c, "CMD-ERR action=%s reason=not_authorized", action);
        return;
    }

    // reject if battery <10
    if (battery < 10) {
        send_line(c, "CMD-ERR action=%s reason=battery_low", action);
        log_msg("Refused %s (battery %d%%)", action, battery);
        return;
    }

    // implement actions
    if (strcmp(action, "SPEED_UP")==0) {
        pthread_mutex_lock(&vehicle.lock);
        if (vehicle.speed >= 30.0) {
            pthread_mutex_unlock(&vehicle.lock);
            send_line(c, "CMD-ERR action=SPEED_UP reason=speed_limit");
            return;
        }
        vehicle.speed += 2.5;
        pthread_mutex_unlock(&vehicle.lock);
        send_line(c, "CMD-ACK action=SPEED_UP status=OK");
        log_msg("CMD executed SPEED_UP by admin");
    } else if (strcmp(action, "SLOW_DOWN")==0) {
        pthread_mutex_lock(&vehicle.lock);
        if (vehicle.speed <= 0.0) vehicle.speed = 0.0;
        else vehicle.speed -= 2.5;
        pthread_mutex_unlock(&vehicle.lock);
        send_line(c, "CMD-ACK action=SLOW_DOWN status=OK");
        log_msg("CMD executed SLOW_DOWN by admin");
    } else if (strcmp(action, "TURN_LEFT")==0) {
        pthread_mutex_lock(&vehicle.lock);
        vehicle.direction_deg = (vehicle.direction_deg + 270) % 360;
        pthread_mutex_unlock(&vehicle.lock);
        send_line(c, "CMD-ACK action=TURN_LEFT status=OK");
        log_msg("CMD executed TURN_LEFT by admin");
    } else if (strcmp(action, "TURN_RIGHT")==0) {
        pthread_mutex_lock(&vehicle.lock);
        vehicle.direction_deg = (vehicle.direction_deg + 90) % 360;
        pthread_mutex_unlock(&vehicle.lock);
        send_line(c, "CMD-ACK action=TURN_RIGHT status=OK");
        log_msg("CMD executed TURN_RIGHT by admin");
    } else {
        send_line(c, "CMD-ERR action=%s reason=unknown_command", action);
    }
}

/* parse incoming line and handle commands */
static void handle_line(client_t *c, const char *line) {
    // log incoming
    char id[64]; client_idstr(c, id, sizeof(id));
    log_msg("<- %s  %s", id, line);

    // tokenize first word
    char copy[MAXLINE]; strncpy(copy, line, sizeof(copy)-1); copy[sizeof(copy)-1]=0;
    char *cmd = strtok(copy, " \t\r\n");
    if (!cmd) { send_line(c, "ERR invalid"); return; }

    if (strcmp(cmd, "AUTH")==0) {
        // AUTH username password   OR AUTH token=<token>
        char *rest = strtok(NULL, "\r\n");
        if (!rest) { send_line(c, "AUTH-ERR reason=missing_credentials"); return; }

        // check token=...
        if (strncmp(rest, "token=", 6)==0) {
            char *token = rest + 6;
            pthread_mutex_lock(&clients_lock);
            // check if any client holds token (assign to this connection)
            client_t *it = clients;
            bool valid = false;
            while (it) {
                if (strcmp(it->token, token)==0) { valid = true; break; }
                it = it->next;
            }
            pthread_mutex_unlock(&clients_lock);
            if (valid) {
                c->authenticated = true;
                strncpy(c->token, token, sizeof(c->token)-1);
                c->role = ROLE_ADMIN; // token only created for admins
                send_line(c, "AUTH-OK token=%s", c->token);
                log_msg("AUTH via token OK for %s", id);
            } else {
                send_line(c, "AUTH-ERR reason=invalid_token");
            }
            return;
        }

        // else username password
        char *user = strtok(rest, " ");
        char *pass = strtok(NULL, " ");
        if (!user || !pass) { send_line(c, "AUTH-ERR reason=bad_format"); return; }

        if (!cred_present) {
            send_line(c, "AUTH-ERR reason=no_credentials_on_server");
            return;
        }

        if (strcmp(user, admin_cred.username) != 0) {
            send_line(c, "AUTH-ERR reason=invalid_user");
            return;
        }
        char myhex[SHA256_DIGEST_LENGTH*2+1];
        sha256_hex_of(admin_cred.salt, pass, myhex);
        if (strcmp(myhex, admin_cred.hashhex)==0) {
            // success: mark admin and generate token
            c->authenticated = true;
            c->role = ROLE_ADMIN;
            gen_token(c->token);
            send_line(c, "AUTH-OK token=%s", c->token);
            log_msg("AUTH OK for admin %s (issued token=%s)", id, c->token);
        } else {
            send_line(c, "AUTH-ERR reason=invalid_password");
        }
    } else if (strcmp(cmd, "SUBSCRIBE")==0) {
        // SUBSCRIBE ADMIN | OBSERVER
        char *role = strtok(NULL, " \r\n");
        if (!role) { send_line(c, "ERR missing role"); return; }
        if (strcasecmp(role, "ADMIN")==0) {
            c->role = ROLE_ADMIN; // but must auth to perform admin actions
            send_line(c, "SUBSCRIBE-OK role=ADMIN");
        } else {
            c->role = ROLE_OBSERVER;
            send_line(c, "SUBSCRIBE-OK role=OBSERVER");
        }
        c->authenticated = c->authenticated; // no change
    } else if (strcmp(cmd, "LIST_USERS")==0) {
        if (!c->authenticated || c->role != ROLE_ADMIN) {
            send_line(c, "ERR not_authorized");
            return;
        }
        char buf[8192];
        build_users_list(buf, sizeof(buf));
        send_line(c, "%s", buf);
    } else if (strcmp(cmd, "CMD")==0) {
        process_cmd(c, line);
    } else if (strcmp(cmd, "QUIT")==0) {
        send_line(c, "BYE");
        // client thread will close and cleanup
        shutdown(c->fd, SHUT_RDWR);
    } else {
        send_line(c, "ERR unknown_command");
    }
}

/* read loop per client */
static void *client_thread(void *arg) {
    client_t *c = (client_t*)arg;
    char buf[MAXLINE];
    ssize_t n;
    // nonblocking read? use simple blocking with recv
    while (1) {
        n = recv(c->fd, buf, sizeof(buf)-1, 0);
        if (n <= 0) break;
        buf[n] = 0;
        // may receive many lines; split by newline
        char *start = buf;
        char *nl;
        while ((nl = strchr(start, '\n')) != NULL) {
            *nl = 0;
            // trim CR
            size_t L = strlen(start);
            if (L && start[L-1] == '\r') start[L-1] = 0;
            if (strlen(start) > 0) handle_line(c, start);
            start = nl + 1;
        }
        // if leftover (no newline), handle it as a line
        if (*start) handle_line(c, start);
    }
    char id[64]; client_idstr(c, id, sizeof(id));
    log_msg("Client %s disconnected", id);
    close(c->fd);
    remove_client(c);
    free(c);
    return NULL;
}

/* accept loop */
static void *acceptor_thread(void *arg) {
    (void)arg;
    while (1) {
        struct sockaddr_in cliaddr;
        socklen_t len = sizeof(cliaddr);
        int connfd = accept(listen_fd, (struct sockaddr*)&cliaddr, &len);
        if (connfd < 0) {
            if (errno == EINTR) continue;
            log_msg("accept error: %s", strerror(errno));
            continue;
        }
        client_t *c = calloc(1, sizeof(client_t));
        c->fd = connfd;
        c->addr = cliaddr;
        c->role = ROLE_NONE;
        c->authenticated = false;
        c->token[0]=0;
        add_client(c);

        char id[64]; client_idstr(c, id, sizeof(id));
        log_msg("New connection from %s", id);

        pthread_create(&c->thread, NULL, client_thread, c);
        pthread_detach(c->thread);
    }
    return NULL;
}

/* signal handler to cleanup */
static void handle_sigint(int s) {
    log_msg("Shutting down...");
    if (listen_fd >= 0) close(listen_fd);
    if (logf) fclose(logf);
    exit(0);
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <port> <LogsFile>\n", argv[0]);
        return 1;
    }
    int port = atoi(argv[1]);
    logs_path = argv[2];
    logf = fopen(logs_path, "a");
    if (!logf) {
        fprintf(stderr, "Cannot open log file %s\n", logs_path);
        return 1;
    }

    signal(SIGINT, handle_sigint);
    // load credentials from ./credentials.txt if exists
    if (!load_credentials("./credentials.txt")) {
        log_msg("Warning: credentials.txt not found or invalid. Create credentials.txt with format: username:salt:hexsha256(salt+password)");
        // still continue, admin disabled until credentials present
    } else {
        log_msg("Credentials loaded for user %s", admin_cred.username);
    }

    // init vehicle
    vehicle.speed = 0.0;
    vehicle.battery = 100;
    vehicle.direction_deg = 0;
    pthread_mutex_init(&vehicle.lock, NULL);

    // create listening socket
    listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    int opt = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    struct sockaddr_in servaddr;
    memset(&servaddr, 0, sizeof(servaddr));
    servaddr.sin_family = AF_INET;
    servaddr.sin_addr.s_addr = INADDR_ANY;
    servaddr.sin_port = htons(port);
    if (bind(listen_fd, (struct sockaddr*)&servaddr, sizeof(servaddr)) < 0) {
        log_msg("bind error: %s", strerror(errno));
        return 1;
    }
    if (listen(listen_fd, 16) < 0) {
        log_msg("listen error: %s", strerror(errno));
        return 1;
    }
    log_msg("Server listening on port %d", port);

    // start acceptor thread
    pthread_t acceptor;
    pthread_create(&acceptor, NULL, acceptor_thread, NULL);

    // start broadcaster
    pthread_t broadcaster;
    pthread_create(&broadcaster, NULL, broadcaster_thread, NULL);

    // join acceptor (never returns)
    pthread_join(acceptor, NULL);

    return 0;
}
