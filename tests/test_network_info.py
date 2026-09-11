import socket
import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'wayland-kiosk'))
from network_info import network_lines, usable_address, timed_out, NetworkDiagnosticTimeout


class NetworkInfoTests(unittest.TestCase):
    def test_route_source_not_server_or_host_address(self):
        for family, source, destination, prefix in [
            (socket.AF_INET, '172.30.33.7', '172.30.32.1', 32),
            (socket.AF_INET6, 'fd00::7', 'fd00::1', 128),
        ]:
            with self.subTest(source=source):
                endpoint = (destination, 8123)
                client = MagicMock()
                client.__enter__.return_value = client
                client.getsockname.return_value = (source, 43210)
                with patch('network_info.socket.getaddrinfo', side_effect=[socket.gaierror(), [(family, socket.SOCK_DGRAM, 0, '', endpoint)]]), patch('network_info.socket.socket', return_value=client):
                    lines = network_lines('http://homeassistant:8123/private?token=secret')
                self.assertIn(f'Trusted-network CIDR (direct HA route): {source}/{prefix}', lines)
                self.assertIn(f'App internal IP address(es): {source}', lines)
                self.assertNotIn('secret', '\n'.join(lines))
                client.connect.assert_called_once_with(endpoint)
                client.send.assert_not_called()
                client.sendto.assert_not_called()

    def test_dns_failure_is_nonfatal(self):
        with patch('network_info.socket.getaddrinfo', side_effect=socket.gaierror()):
            lines = network_lines('http://homeassistant:8123')
        self.assertIn('App internal IP address(es): unavailable', lines)
        self.assertFalse(any(line.startswith('Trusted-network CIDR') for line in lines))

    def test_unusable_addresses_are_not_trusted(self):
        for value in ['127.0.0.1', '::1', '0.0.0.0', 'fe80::1%eth0', '224.0.0.1']:
            self.assertIsNone(usable_address(value))

    def test_deadline_is_not_swallowed_as_dns_failure(self):
        with patch('network_info.socket.getaddrinfo', side_effect=NetworkDiagnosticTimeout):
            with self.assertRaises(NetworkDiagnosticTimeout):
                network_lines('http://homeassistant:8123')
        with self.assertRaises(NetworkDiagnosticTimeout):
            timed_out(None, None)
