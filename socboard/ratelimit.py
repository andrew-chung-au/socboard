"""In-process limits on failed logins (#178), and the one place the client address is read.

Counters live in this process and assume one worker (decision 9 of the Deploy record,
`_docs/decisions/2026-11-13T2156Z-deploy.md`); they reset on restart and a second worker would
multiply the limits.

Failures are counted twice: per (client address, username) and per client address. The seeded
usernames are public, so a limit on the username alone would let any outsider lock out every
reviewer; keyed on the client address, an outsider can only limit their own address. Each key is
a sha256 digest, so memory per key does not depend on the length of a submitted username, and the
table holds at most `max_tracked` keys (the least recently failed one is dropped first). A key
that drops out only loses its count: dropping can never refuse anyone.

The client address is the socket peer. `X-Forwarded-For` is read only when the peer is one of
`SOCBOARD_TRUSTED_PROXIES`, and then the answer is the rightmost entry that is not itself a
trusted proxy, which is the address the nearest trusted proxy saw. A caller can write the left
side of that header and nothing to its right.
"""

import hashlib
import ipaddress
import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

MAX_FAILURES_VAR = "SOCBOARD_LOGIN_MAX_FAILURES"
CLIENT_MAX_FAILURES_VAR = "SOCBOARD_LOGIN_CLIENT_MAX_FAILURES"
WINDOW_SECONDS_VAR = "SOCBOARD_LOGIN_WINDOW_SECONDS"
MAX_TRACKED_VAR = "SOCBOARD_LOGIN_MAX_TRACKED"
TRUSTED_PROXIES_VAR = "SOCBOARD_TRUSTED_PROXIES"

DEFAULT_MAX_FAILURES = 5
DEFAULT_CLIENT_MAX_FAILURES = 20
DEFAULT_WINDOW_SECONDS = 300
DEFAULT_MAX_TRACKED = 10000

FORWARDED_HEADER = "x-forwarded-for"
UNKNOWN_CLIENT = "unknown"


class LimitConfigError(ValueError):
    """A limit setting is unusable. The message names the variable only, never its value."""


@dataclass(frozen=True)
class LoginLimits:
    max_failures: int
    client_max_failures: int
    window_seconds: int
    max_tracked: int
    trusted_proxies: frozenset[str]


def _whole_number(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name)
    if raw is None:
        return default
    text = raw.strip()
    if not (text.isascii() and text.isdigit()) or int(text) < 1:
        raise LimitConfigError(f"{name} must be a whole number of 1 or more.")
    return int(text)


def normalise_address(text: str) -> str | None:
    """The canonical form of an IP address, or None when `text` is not one."""
    try:
        address = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.compressed
    return address.compressed


def _trusted_proxies(source: Mapping[str, str]) -> frozenset[str]:
    raw = source.get(TRUSTED_PROXIES_VAR, "")
    found: set[str] = set()
    for part in raw.split(","):
        if not part.strip():
            continue
        address = normalise_address(part)
        if address is None:
            raise LimitConfigError(f"{TRUSTED_PROXIES_VAR} must be a comma-separated list of IP addresses.")
        found.add(address)
    return frozenset(found)


def resolve_limits(source: Mapping[str, str]) -> LoginLimits:
    """The limits from the environment, or `LimitConfigError`. An empty value is refused except
    for the proxy list, where empty means none are trusted."""
    return LoginLimits(
        max_failures=_whole_number(source, MAX_FAILURES_VAR, DEFAULT_MAX_FAILURES),
        client_max_failures=_whole_number(source, CLIENT_MAX_FAILURES_VAR, DEFAULT_CLIENT_MAX_FAILURES),
        window_seconds=_whole_number(source, WINDOW_SECONDS_VAR, DEFAULT_WINDOW_SECONDS),
        max_tracked=_whole_number(source, MAX_TRACKED_VAR, DEFAULT_MAX_TRACKED),
        trusted_proxies=_trusted_proxies(source),
    )


def client_address(
    peer: str | None, forwarded: list[str], trusted_proxies: frozenset[str]
) -> str:
    """The client's address: the one place it is decided.

    `peer` is the socket peer (None when the server gives none) and `forwarded` the values of
    every `X-Forwarded-For` header. Anything that is not a trusted proxy's own connection ignores
    the header, so a forged header cannot choose a bucket.
    """
    if peer is None:
        return UNKNOWN_CLIENT
    peer_address = normalise_address(peer)
    if peer_address is None or peer_address not in trusted_proxies:
        return peer_address or peer
    entries = [part.strip() for value in forwarded for part in value.split(",")]
    for entry in reversed(entries):
        address = normalise_address(entry)
        if address is None:
            # Not an address: the proxy would not have written it. Use the proxy's own bucket.
            return peer_address
        if address not in trusted_proxies:
            return address
    return peer_address


def _digest(*parts: str) -> bytes:
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8", "surrogatepass")).digest()


def _username_key(username: str) -> str:
    return username.strip().casefold()


@dataclass(frozen=True)
class Reservation:
    """The answer of `LoginLimiter.reserve`: `retry_after` seconds when refused, else None."""

    retry_after: int | None
    stamp: float


class LoginLimiter:
    """Sliding-window counters of failed logins. Thread-safe: routes run in a thread pool."""

    def __init__(self, limits: LoginLimits, clock: Callable[[], float] = time.monotonic) -> None:
        self._limits = limits
        self._clock = clock
        self._lock = threading.Lock()
        # key digest -> failure times, oldest first; most recently failed key last.
        self._failures: OrderedDict[bytes, deque[float]] = OrderedDict()

    def _keys(self, client: str, username: str) -> tuple[tuple[bytes, int], tuple[bytes, int]]:
        user_key = _digest("user", client, _username_key(username))
        client_key = _digest("client", client)
        return (
            (user_key, self._limits.max_failures),
            (client_key, self._limits.client_max_failures),
        )

    def reserve(self, client: str, username: str) -> Reservation:
        """Check the limits and, when the attempt is allowed, count it as a failure at once.

        The check and the count happen under one lock, before any password work, so a burst of
        concurrent attempts cannot all pass the check before the first is counted. A login that
        then succeeds calls `release`; one that fails leaves the count in place.
        """
        now = self._clock()
        window = self._limits.window_seconds
        wait = 0.0
        with self._lock:
            keys = self._keys(client, username)
            for key, limit in keys:
                times = self._failures.get(key)
                if times is None:
                    continue
                while times and times[0] <= now - window:
                    times.popleft()
                if not times:
                    del self._failures[key]
                elif len(times) >= limit:
                    # The oldest failure counted leaves the window at times[0] + window.
                    wait = max(wait, times[0] + window - now)
            if wait > 0:
                return Reservation(retry_after=max(1, math.ceil(wait)), stamp=now)
            for key, limit in keys:
                times = self._failures.get(key)
                if times is None:
                    times = self._failures[key] = deque(maxlen=limit)
                times.append(now)
                self._failures.move_to_end(key)
            while len(self._failures) > self._limits.max_tracked:
                self._failures.popitem(last=False)
        return Reservation(retry_after=None, stamp=now)

    def release(self, client: str, username: str, reservation: Reservation) -> None:
        """Take back the count of an attempt that turned out to be a successful login.

        Removes one timestamp equal to the reservation's from each key, when it is still there
        (it may have aged out or been dropped with its key)."""
        with self._lock:
            for key, _ in self._keys(client, username):
                times = self._failures.get(key)
                if times is None:
                    continue
                try:
                    times.remove(reservation.stamp)
                except ValueError:
                    continue
                if not times:
                    del self._failures[key]

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._failures)
