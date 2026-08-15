CC ?= $(CROSS_COMPILE)gcc
CFLAGS += -Wall -Wextra -I../ta/include
LDADD += -lteec

BINARY = veriswarm_optee_ca

.PHONY: all
all: $(BINARY)

$(BINARY): main.c
	$(CC) $(CFLAGS) -o $@ $< $(LDADD)

.PHONY: clean
clean:
	rm -f $(BINARY)
