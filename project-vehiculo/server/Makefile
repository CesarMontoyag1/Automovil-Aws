CC = gcc
CFLAGS = -Wall -Wextra -pthread
LDFLAGS = -lcrypto

all: server

server: server.c
	$(CC) $(CFLAGS) server.c -o server $(LDFLAGS)

clean:
	rm -f server
