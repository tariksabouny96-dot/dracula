"""Exec helper run inside a fresh network namespace (``unshare -rn``).

A new namespace has only a loopback interface, and it starts DOWN. This brings
``lo`` up so sandboxed tests can talk to a local server on 127.0.0.1, then
replaces itself with the requested command. No other interface exists, so the
child still has no route off the machine.

Isolation never depends on this helper: the empty namespace does that. Some
kernels refuse the classic ioctl (gVisor answers ENOTTY), so the same request is
sent over netlink (what ``ip link set lo up`` does); if loopback still can't be
brought up the command runs without 127.0.0.1 and says so on stderr.
"""
import fcntl
import os
import socket
import struct
import sys

SIOCGIFFLAGS, SIOCSIFFLAGS, IFF_UP = 0x8913, 0x8914, 0x1
RTM_NEWLINK, NLM_F_REQUEST, NLM_F_ACK, NLMSG_ERROR = 16, 0x1, 0x4, 2


def _ioctl_up() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        request = struct.pack("16sH14x", b"lo", 0)
        flags = struct.unpack("16sH14x", fcntl.ioctl(sock.fileno(), SIOCGIFFLAGS, request))[1]
        if flags & IFF_UP:
            return True
        fcntl.ioctl(sock.fileno(), SIOCSIFFLAGS, struct.pack("16sH14x", b"lo", flags | IFF_UP))
    return True


def _netlink_up() -> bool:
    """RTM_NEWLINK on lo with IFF_UP set (and only IFF_UP changed); True when the kernel acks it."""
    body = struct.pack("=BxHiII", socket.AF_UNSPEC, 0, socket.if_nametoindex("lo"), IFF_UP, IFF_UP)
    msg = struct.pack("=LHHLL", 16 + len(body), RTM_NEWLINK, NLM_F_REQUEST | NLM_F_ACK, 1, 0) + body
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0) as nl:   # 0 = NETLINK_ROUTE
        nl.settimeout(5)
        nl.sendto(msg, (0, 0))
        reply = nl.recv(4096)
    if len(reply) < 20:
        return False
    _, kind, _, _, _ = struct.unpack("=LHHLL", reply[:16])
    return kind == NLMSG_ERROR and struct.unpack("=i", reply[16:20])[0] == 0


def loopback_up() -> bool:
    for attempt in (_ioctl_up, _netlink_up):
        try:
            if attempt():
                return True
        except (OSError, ValueError):
            continue
    return False


def main() -> None:
    if not loopback_up():
        sys.stderr.write("hood-sandbox: loopback could not be brought up here; 127.0.0.1 is unavailable "
                         "inside the sandbox (still no network)\n")
        sys.stderr.flush()
    os.execv(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
