"""Report container addresses and the local route to HA without sending traffic."""
import ipaddress
import signal
import socket
from urllib.parse import urlsplit

from security_config import effective_ha_url, load_options


def usable_address(value):
    address = ipaddress.ip_address(value.split('%', 1)[0])
    if address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast:
        return None
    return str(address)


def network_lines(url):
    addresses = set()
    routes = set()
    try:
        for _, _, _, _, endpoint in socket.getaddrinfo(socket.gethostname(), 0, type=socket.SOCK_DGRAM):
            address = usable_address(endpoint[0])
            if address:
                addresses.add(address)
    except OSError:
        pass
    target = urlsplit(url)
    try:
        endpoints = socket.getaddrinfo(target.hostname, target.port or (443 if target.scheme == 'https' else 80), type=socket.SOCK_DGRAM)
        for family, kind, protocol, _, endpoint in endpoints:
            try:
                with socket.socket(family, kind, protocol) as probe:
                    # UDP connect selects a local route; no packet is sent.
                    probe.connect(endpoint)
                    source = usable_address(probe.getsockname()[0])
                if source:
                    addresses.add(source)
                    routes.add((source, endpoint[0]))
            except OSError:
                continue
    except OSError:
        pass
    lines = ['App internal IP address(es): ' + (', '.join(sorted(addresses)) or 'unavailable')]
    for source, destination in sorted(routes):
        address = ipaddress.ip_address(source)
        lines.append(f'Home Assistant route: app {source} -> server {destination}')
        lines.append(f'Trusted-network CIDR (direct HA route): {source}/{address.max_prefixlen}')
    if not routes:
        lines.append('Home Assistant route address unavailable; check DNS/network and restart to report it again.')
    lines.append('Trust only the app address and a dedicated HA user. Recheck after reinstall/network changes; a proxy or NAT may change the address HA sees.')
    return lines


class NetworkDiagnosticTimeout(Exception):
    """Abort the complete diagnostic, including DNS resolution."""


def timed_out(signum, frame):
    raise NetworkDiagnosticTimeout('network diagnostics timed out')


if __name__ == '__main__':
    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(5)
    try:
        for line in network_lines(effective_ha_url(load_options())):
            print(line, flush=True)
    except (OSError, ValueError, NetworkDiagnosticTimeout):
        print('App network diagnostics unavailable; startup will continue.', flush=True)
    finally:
        signal.alarm(0)
