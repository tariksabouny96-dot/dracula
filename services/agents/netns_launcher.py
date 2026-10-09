"""Exec helper run inside a fresh network namespace (``unshare -rn``).

A new namespace has only a loopback interface, and it starts DOWN. This brings
``lo`` up so sandboxed tests can talk to a local server on 127.0.0.1, then
replaces itself with the requested command. No other interface exists, so the
child still has no route off the machine.
"""
import fcntl
import os
import socket
import struct
import sys

SIOCGIFFLAGS, SIOCSIFFLAGS, IFF_UP = 0x8913, 0x8914, 0x1


def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    request = struct.pack("16sH14x", b"lo", 0)
    flags = struct.unpack("16sH14x", fcntl.ioctl(sock.fileno(), SIOCGIFFLAGS, request))[1]
    fcntl.ioctl(sock.fileno(), SIOCSIFFLAGS, struct.pack("16sH14x", b"lo", flags | IFF_UP))
    sock.close()
    os.execv(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
