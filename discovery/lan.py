"""Explicit trusted-LAN sharing of this installation, with in-memory pairing."""
import hmac
import ipaddress
import secrets
import socket
from http.cookies import SimpleCookie


def loopback(address):
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def private_address(address):
    try:
        ip = ipaddress.ip_address(address)
        return any(ip in network for network in (
            ipaddress.ip_network('10.0.0.0/8'),
            ipaddress.ip_network('172.16.0.0/12'),
            ipaddress.ip_network('192.168.0.0/16')))
    except ValueError:
        return False


def local_addresses():
    try:
        return sorted({row[4][0] for row in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET)
            if private_address(row[4][0])})
    except OSError:
        return []


class LanAccess:
    # The private URL and cookie authorize use of this PC's signed-in profile.
    # They expire on restart and are never written to the preference store.
    def __init__(self):
        self.addresses = local_addresses()
        self._key = secrets.token_urlsafe(32)

    def rotate(self):
        self._key = secrets.token_urlsafe(32)

    def paired(self, headers):
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get('Cookie', ''))
            value = cookie.get('cad_lan')
            return bool(value and self.accept(value.value))
        except Exception:
            return False

    def accept(self, value):
        return (isinstance(value, str) and value.isascii() and len(value) == len(self._key)
                and hmac.compare_digest(value, self._key))

    def pair_cookie(self):
        return 'cad_lan=' + self._key + '; HttpOnly; SameSite=Strict; Path=/'

    def links(self, port):
        return [f'http://{address}:{port}/#pair={self._key}'
                for address in self.addresses]
