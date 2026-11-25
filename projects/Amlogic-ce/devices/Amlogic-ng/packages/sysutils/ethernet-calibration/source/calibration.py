# SPDX-License-Identifier: GPL-2.0-or-later
"""Native calibration policy; hardware writes use the versioned driver ABI."""

import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from public_servers import PUBLIC_SERVERS

BACKEND = 'am6b-rtl8211f-v1'
PROFILE_DIR = Path('/storage/.config/ethernet-calibration')
NET_ROOT = Path('/sys/class/net')
DT_ROOT = Path('/sys/firmware/devicetree/base')
MIN_BPS = 800_000_000
MAX_LAN_TRAFFIC_SECONDS = 320
MAX_LAN_TRAFFIC_BYTES = 48 * 1024 ** 3
MAX_VERIFICATION_ATTEMPTS = 2
MAX_JOB_SECONDS = 1500
MIN_HEALTH_BYTES = 64 * 1024 ** 2
PUBLIC_RATE = 10_000_000
PUBLIC_SECONDS = 20
PUBLIC_PAYLOAD = 25_000_000
PUBLIC_MAX_BYTES = 32 * 1024 ** 2
PUBLIC_REPAIR_MAX_BYTES = 256 * 1024 ** 2
PUBLIC_REPAIR_MAX_SECONDS = 1200
HTTPS_BACKEND = 'cloudflare_https_experimental'
HTTPS_ENDPOINT = 'https://speed.cloudflare.com/__down'


class CalibrationError(Exception):
    pass


class Cancelled(CalibrationError):
    pass


class Inconclusive(CalibrationError):
    pass


class CandidateError(CalibrationError):
    pass


class CRCError(CandidateError):
    pass


class RecoveryTimeout(CalibrationError):
    pass


def endpoint(address, port, public=False, consent=False):
    try:
        parsed = ipaddress.IPv4Address(address)
    except (ipaddress.AddressValueError, TypeError) as error:
        raise CalibrationError('Enter a valid IPv4 server address') from error
    if type(port) is not int or not 1 <= port <= 65535:
        raise CalibrationError('Server port must be between 1 and 65535')
    if parsed.is_loopback or parsed.is_multicast or parsed.is_unspecified or parsed.is_link_local:
        raise CalibrationError('This address is not a traffic test server')
    if parsed.is_global and not (public and consent):
        raise CalibrationError('Public traffic requires explicit permission and cost acknowledgement')
    if public and not consent:
        raise CalibrationError('Public traffic requires explicit permission and cost acknowledgement')
    return str(parsed), port


def public_server(identifier, port=None):
    for server in PUBLIC_SERVERS:
        if identifier == server['id']:
            if server['unsupported_reason']:
                raise CalibrationError(server['unsupported_reason'])
            selected_port = server['port'] if port is None else port
            if type(selected_port) is not int or not server['port_first'] <= selected_port <= server['port_last']:
                raise CalibrationError('Choose a TCP port listed for this public server')
            return dict(server, port=selected_port)
    raise CalibrationError('Unknown public server selection')


def resolve_public_server(server, cancel, deadline=None):
    if cancel.is_set():
        raise Cancelled('Ethernet error check cancelled')
    script = ("import json,socket,sys;"
              "values=socket.getaddrinfo(sys.argv[1],None,socket.AF_INET,socket.SOCK_STREAM);"
              "print(json.dumps(sorted({item[4][0] for item in values})))")
    try:
        timeout = 5 if deadline is None else min(5, deadline-time.monotonic())
        if timeout <= 0:
            raise CalibrationError('Public traffic deadline reached before DNS')
        output = run_command([sys.executable, '-I', '-B', '-c', script, server['hostname']],
                             timeout=timeout, cancel=cancel)
        addresses = json.loads(output)
    except Cancelled:
        raise
    except (CalibrationError, OSError, ValueError) as error:
        raise Inconclusive('Public server DNS lookup failed or timed out: ' +
                               server['hostname']) from error
    if not isinstance(addresses, list) or not addresses or len(addresses) > 16:
        raise CalibrationError('Public server DNS returned no usable IPv4 addresses')
    try:
        parsed = [ipaddress.IPv4Address(value) for value in addresses]
    except (ipaddress.AddressValueError, TypeError) as error:
        raise CalibrationError('Public server DNS returned invalid IPv4 addresses') from error
    if any(not value.is_global for value in parsed):
        raise CalibrationError('Public server DNS returned a non-public IPv4 address')
    return str(min(parsed, key=int))


def onboard_route(route, name):
    fields = route.split()
    return (bool(fields) and fields[0] != 'local' and 'dev' in fields and
            fields.index('dev') + 1 < len(fields) and
            fields[fields.index('dev') + 1] == name)


def choose_window(clean, previous=None):
    windows = []
    for base in (0, 16):
        start = None
        for index in range(base, base + 17):
            if index < base + 16 and index in clean:
                if start is None:
                    start = index
            elif start is not None:
                windows.append((start, index - 1))
                start = None
    if not windows:
        raise CalibrationError('No clean timing candidates; check cable, switch and server')
    windows.sort(key=lambda pair: (
        -(pair[1] - pair[0] + 1),
        not (previous is not None and pair[0] <= previous <= pair[1]),
        pair[0]))
    start, end = windows[0]
    chosen = previous if end - start < 2 and previous in range(start, end + 1) else (start + end) // 2
    return chosen, [start, end]


def counter_delta(before, after):
    if before.get('generation') != after.get('generation'):
        raise CalibrationError('Ethernet was reopened/reset during measurement')
    if (before.keys() != after.keys() or
            any(value < 0 or after[key] < value for key, value in before.items())):
        raise CalibrationError('Network counters reset; measurement is inconclusive')
    return {key: after[key] - value for key, value in before.items()}


def verify_measurement(result, before, after, reverse, seconds):
    try:
        if not isinstance(result, dict):
            raise ValueError('Invalid iperf3 document')
        if result.get('error'):
            raise CalibrationError('Traffic server did not complete the test')
        end = result['end']
        received = end['sum_received']
        sent = end['sum_sent']
        bps = received['bits_per_second']
        payload = received['bytes'] if reverse else sent['bytes']
        duration = received['seconds']
        retransmits = sent['retransmits']
        numbers = (bps, payload, duration, retransmits)
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
               for value in numbers):
            raise ValueError('Invalid measurement values')
    except (KeyError, TypeError, ValueError) as error:
        raise CalibrationError('Malformed or incomplete iperf3 measurement') from error
    delta = counter_delta(before, after)
    if any(value for key, value in delta.items() if key.startswith('crc:')):
        raise CandidateError('New Ethernet CRC corruption during traffic')
    if duration < seconds * 0.9 or bps < MIN_BPS or retransmits != 0:
        raise CandidateError('Insufficient clean gigabit traffic; endpoint or path may be limiting')
    if payload < MIN_BPS * seconds * 0.9 / 8:
        raise CalibrationError('Insufficient traffic payload; measurement is inconclusive')
    if delta['rx_bytes' if reverse else 'tx_bytes'] < payload * 0.9:
        raise CalibrationError('Traffic did not traverse the selected onboard adapter')
    return {'bps': bps, 'bytes': payload, 'seconds': duration,
            'retransmits': retransmits, 'crc_delta': 0}


def read_json(path):
    if path.stat().st_size > 64 * 1024:
        raise CalibrationError('Profile is too large')
    with path.open() as stream:
        result = json.load(stream)
    if not isinstance(result, dict):
        raise CalibrationError('Invalid calibration profile document')
    return result


def validate_profile(profile, identity):
    if (not isinstance(profile, dict) or type(profile.get('schema')) is not int or
            profile.get('schema') not in (1, 2, 3, 4)):
        raise CalibrationError('Unsupported calibration profile schema')
    if profile.get('identity') != identity:
        raise CalibrationError('Saved profile no longer matches the adapter/backend; recalibrate')
    index = profile.get('index')
    if type(index) is not int or not 0 <= index <= 31:
        raise CalibrationError('Invalid saved timing index')
    if profile['schema'] == 3:
        stamp = profile.get('manually_set_at')
        if (profile.get('kind') != 'manual_unverified' or
                type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= 0 or
                any(key in profile for key in ('verification', 'verified_at', 'window'))):
            raise CalibrationError('Invalid manually configured profile; no verification may be claimed')
        return index
    if profile['schema'] in (2, 4):
        validate_public_profile(profile)
        return index
    if profile.get('kind') not in (None, 'lan_gigabit'):
        raise CalibrationError('Unsupported gigabit profile type')
    tests = profile.get('verification')
    if not isinstance(tests, dict):
        raise CalibrationError('Saved profile has no verification evidence')
    window = profile.get('window')
    narrow = False
    if window is not None:
        if (not isinstance(window, list) or len(window) != 2 or
                any(type(value) is not int for value in window) or
                not 0 <= window[0] <= index <= window[1] <= 31 or
                window[0] // 16 != window[1] // 16):
            raise CalibrationError('Invalid saved timing window')
        narrow = window[1] - window[0] < 2
    rounds = tests.get('rounds')
    if narrow or rounds is not None:
        if (not isinstance(rounds, list) or len(rounds) != (2 if narrow else 1) or
                any(not isinstance(item, dict) for item in rounds) or
                any(tests.get(direction) != rounds[-1].get(direction)
                    for direction in ('send', 'receive'))):
            raise CalibrationError('Saved profile lacks required repeated verification')
    for measurement in [tests] + (rounds or []):
        for direction in ('send', 'receive'):
            evidence = measurement.get(direction)
            if not isinstance(evidence, dict):
                raise CalibrationError('Saved profile has incomplete verification evidence')
            try:
                values = [evidence[key] for key in ('bps', 'bytes', 'seconds', 'retransmits', 'crc_delta')]
                if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
                    raise ValueError('Invalid evidence')
                if (evidence['bps'] < MIN_BPS or evidence['seconds'] < 27 or
                        evidence['bytes'] < MIN_BPS * 27 / 8 or
                        evidence['retransmits'] != 0 or evidence['crc_delta'] != 0):
                    raise ValueError('Unverified profile')
            except (KeyError, TypeError, ValueError) as error:
                raise CalibrationError('Saved profile failed verification criteria') from error
    return index


def validate_public_profile(profile):
    if profile.get('kind') != 'public_error_checked':
        raise CalibrationError('Unsupported reduced-evidence profile type')
    try:
        rounds = profile['verification']['rounds']
        if not isinstance(rounds, list) or len(rounds) != 2:
            raise ValueError('Two public verification rounds are required')
        https = profile['schema'] == 4
        if https and profile.get('traffic_backend') != HTTPS_BACKEND:
            raise ValueError('Unsupported HTTPS profile backend')
        if https and (type(profile.get('verified_at')) not in (int, float) or
                      not math.isfinite(profile['verified_at']) or profile['verified_at'] <= 0):
            raise ValueError('Invalid HTTPS verification timestamp')
        generations = []
        for evidence in rounds:
            rate_key = 'application_bps' if https else 'bps'
            values = [evidence[key] for key in (rate_key, 'bytes', 'seconds', 'rx_bytes', 'crc_delta')]
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
                raise ValueError('Invalid public evidence')
            if any(type(evidence[key]) is not int for key in
                   ('bytes', 'rx_bytes', 'crc_delta', 'index', 'generation')):
                raise ValueError('Invalid public counter provenance')
            if (not 18 <= evidence['seconds'] <= 21 or
                    not 20_000_000 <= evidence['bytes'] <= PUBLIC_MAX_BYTES or
                    not PUBLIC_RATE * .8 <= evidence[rate_key] <= PUBLIC_RATE * 1.2 or
                    abs(evidence[rate_key] - evidence['bytes'] * 8 / evidence['seconds']) > 1 or
                    evidence['rx_bytes'] < evidence['bytes'] * .9 or evidence['crc_delta'] != 0 or
                    evidence['interface'] != 'eth0' or evidence['index'] != profile['index'] or
                    type(evidence['generation']) is not int or evidence['generation'] < 0):
                raise ValueError('Public verification criteria were not met')
            if https:
                if (evidence.get('backend') != HTTPS_BACKEND or
                        evidence.get('endpoint') != HTTPS_ENDPOINT or
                        evidence.get('tls_verified') is not True or
                        evidence.get('tls_version') not in ('TLSv1.2', 'TLSv1.3') or
                        not isinstance(evidence.get('certificate_sha256'), str) or
                        re.fullmatch('[0-9a-f]{64}', evidence['certificate_sha256']) is None or
                        type(evidence.get('http_status')) is not int or evidence['http_status'] != 200 or
                        type(evidence.get('requested_bytes')) is not int or
                        evidence['requested_bytes'] != PUBLIC_PAYLOAD or evidence['bytes'] != PUBLIC_PAYLOAD or
                        evidence.get('pacing') != 'application_read' or
                        evidence.get('pacing_bps') != PUBLIC_RATE or
                        evidence.get('wire_rate_limited') is not False or
                        evidence.get('wire_bytes_capped') is not False or 'bps' in evidence):
                    raise ValueError('Invalid HTTPS evidence or rate-cap claim')
                framing = evidence.get('framing')
                if (framing not in ('content_length', 'chunked') or
                        (framing == 'content_length' and (
                            type(evidence.get('declared_bytes')) is not int or
                            evidence['declared_bytes'] != PUBLIC_PAYLOAD)) or
                        (framing == 'chunked' and evidence.get('declared_bytes') is not None)):
                    raise ValueError('Invalid exact HTTPS payload framing')
                tcp_bytes, tcp_seconds = evidence.get('tcp_unique_received_bytes'), evidence.get('tcp_progress_seconds')
                if (type(tcp_bytes) is not int or not PUBLIC_PAYLOAD * .9 <= tcp_bytes <= PUBLIC_MAX_BYTES + 512*1024 or
                        type(tcp_seconds) not in (int, float) or not math.isfinite(tcp_seconds) or
                        not 18 <= tcp_seconds <= 21):
                    raise ValueError('HTTPS lacks sustained fresh transport evidence')
            source = ipaddress.IPv4Address(evidence['source'])
            if source.is_loopback or source.is_unspecified or source.is_multicast or source.is_link_local:
                raise ValueError('Invalid onboard source address')
            generations.append(evidence['generation'])
        if generations[1] <= generations[0]:
            raise ValueError('Public verification did not reopen Ethernet')
    except (KeyError, TypeError, ValueError) as error:
        raise CalibrationError('Saved public profile failed repeated error-check criteria') from error


class ProfileStore:
    def __init__(self, directory=PROFILE_DIR):
        self.directory = Path(directory)

    def load(self, name='active'):
        path = self.directory / (name + '.json')
        if not path.exists():
            return None
        try:
            return read_json(path)
        except (OSError, ValueError) as error:
            raise CalibrationError('Cannot read saved calibration profile') from error

    def replace(self, name, profile):
        self.replace_bytes(name, json.dumps(profile, allow_nan=False).encode())

    def replace_bytes(self, name, content):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        fd, temporary = tempfile.mkstemp(prefix='.profile-', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / (name + '.json'))
            self.sync()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def sync(self):
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def remove_active(self):
        path = self.directory / 'active.json'
        if path.exists():
            path.unlink()
            self.sync()

    def snapshot(self):
        result = {}
        for name in ('active', 'previous'):
            path = self.directory / (name + '.json')
            content = None
            if path.exists():
                with path.open('rb') as stream:
                    content = stream.read(64 * 1024 + 1)
                if len(content) > 64 * 1024:
                    raise CalibrationError('Profile is too large for safe rollback; '
                                           'back up and remove the oversized file manually')
            result[name] = content
        return result

    def restore(self, snapshot):
        for name, content in snapshot.items():
            path = self.directory / (name + '.json')
            if content is None:
                if path.exists():
                    path.unlink()
                    self.sync()
            else:
                current = None
                if path.exists():
                    with path.open('rb') as stream:
                        current = stream.read(len(content) + 1)
                if current != content:
                    self.replace_bytes(name, content)

    def save(self, profile, identity, accept):
        validate_profile(profile, identity)
        snapshot = self.snapshot()
        previous = self.load()
        older = self.load('previous')
        if profile['schema'] == 3:
            for existing in (previous, older):
                if existing is not None:
                    validate_profile(existing, identity)
        preserve_lan = (previous is not None and
                        previous.get('kind') in ('public_error_checked', 'manual_unverified') and
                        older is not None and older.get('schema') == 1)
        if preserve_lan:
            validate_profile(older, identity)
        try:
            if previous is not None and not preserve_lan:
                self.replace('previous', previous)
            self.replace('active', profile)
            accept()
        except (OSError, CalibrationError):
            self.restore(snapshot)
            raise


def run_command(arguments, timeout=5, cancel=None, allow_failure=False, poll=None):
    process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled('Calibration cancelled')
            if time.monotonic() >= deadline:
                if arguments[0] == 'iperf3':
                    raise CandidateError('Traffic timed out at this timing candidate')
                raise CalibrationError('Network diagnostic command timed out')
            try:
                out, err = process.communicate(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                if poll is not None:
                    poll()
                continue
        if len(out) > 1024 * 1024 or len(err) > 64 * 1024:
            raise CalibrationError('Traffic command returned excessive output')
        if process.returncode and not allow_failure:
            raise CalibrationError('Network command failed: ' + arguments[0])
        return out.decode('utf-8', errors='strict')
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


class Adapter:
    def __init__(self, root):
        self.root = Path(root)
        self.name = self.root.name
        self.control = self.root / 'ce_calibration'
        self.identity = self.read_identity()

    def capabilities(self):
        result = json.loads(self.control.read_text())
        if not isinstance(result, dict) or result.get('abi') != 1 or result.get('backend') != BACKEND:
            raise CalibrationError('Unsupported Ethernet calibration backend')
        return result

    def read_identity(self):
        self.capabilities()
        model = (DT_ROOT / 'model').read_bytes().rstrip(b'\0').decode()
        dt_id = (DT_ROOT / 'amlogic-dt-id').read_bytes().rstrip(b'\0').decode()
        if model != 'UGOOS AM6B' or dt_id != 'g12b_AM6b_b4g':
            raise CalibrationError('Unsupported board identity')
        return {'backend': BACKEND, 'abi': 1, 'model': model, 'dt_id': dt_id,
                'mac': (self.root / 'address').read_text().strip(),
                'phy_id': '0x001cc916'}

    def write(self, command):
        with self.control.open('w') as stream:
            stream.write(command + '\n')

    def link_ready(self):
        return ((self.root / 'carrier').read_text().strip() == '1' and
                (self.root / 'speed').read_text().strip() == '1000' and
                (self.root / 'duplex').read_text().strip() == 'full')

    def address(self):
        import fcntl
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                result = fcntl.ioctl(probe.fileno(), 0x8915,
                                     struct.pack('256s', self.name.encode('ascii')))
        except OSError as error:
            raise CalibrationError('Onboard IPv4 address is unavailable; configure a manual '
                                   'address in Network settings if DHCP cannot complete') from error
        return socket.inet_ntoa(result[20:24])

    def wait_ready(self, cancel, address=None, poll=None, require_address=False):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if cancel.wait(0.2):
                raise Cancelled('Calibration cancelled')
            if poll is not None:
                poll()
            try:
                if self.link_ready() and (
                        self.address() == address if address is not None else
                        not require_address or self.address() != '0.0.0.0'):
                    return
            except (OSError, CalibrationError):
                continue
        raise CandidateError('Gigabit link/address did not recover; check cable and switch')

    def counters(self):
        result = {key: int((self.root / 'statistics' / key).read_text())
                  for key in ('rx_bytes', 'tx_bytes', 'rx_packets')}
        result['generation'] = self.capabilities()['generation']
        stats = run_command(['ethtool', '-S', self.name])
        for line in stats.splitlines():
            key, separator, value = line.strip().partition(':')
            if separator and key in ('rx_crc', 'mmc_rx_crc_error'):
                result['crc:' + key] = int(value)
        if 'crc:rx_crc' not in result:
            raise CalibrationError('Required Ethernet CRC counter is unavailable')
        return result


def adapters():
    if Path('/dev/.kernel_ipconfig').exists():
        return []
    return [Adapter(root) for root in sorted(NET_ROOT.iterdir())
            if (root / 'ce_calibration').exists()]


class Job:
    def __init__(self, adapter, store, address, port, public, update, preset=None, backend='iperf3'):
        self.adapter = adapter
        self.store = store
        self.backend = backend
        self.preset = None
        if backend == HTTPS_BACKEND:
            if not public or any(value is not None for value in (address, port, preset)):
                raise CalibrationError('Experimental HTTPS requires its selected source, not a custom endpoint')
            self.preset = {'id': HTTPS_BACKEND, 'label': 'Cloudflare HTTPS (experimental)',
                           'hostname': 'speed.cloudflare.com', 'port': 443}
            self.address, self.port = None, 443
        elif backend != 'iperf3':
            raise CalibrationError('Unsupported traffic backend')
        elif preset is not None:
            if not public or address is not None:
                raise CalibrationError('Public presets require the diagnostic mode and no custom address')
            self.preset = public_server(preset, port)
            self.address, self.port = None, self.preset['port']
        else:
            self.address, self.port = endpoint(address, port, public, public)
        self.public = public
        self.notify = update
        self.progress = {'phase': 'starting'}
        self.operation_started = time.monotonic()
        self.update = self.report
        self.cancel = threading.Event()
        self.lease_stop = threading.Event()
        self.lease_error = None
        self.traffic_seconds = 0
        self.traffic_bytes = 0
        self.deadline = time.monotonic() + MAX_JOB_SECONDS
        self.scan_probe = False
        self.last_measurement = None

    def report(self, result):
        if 'phase' in result:
            candidate = {key: self.progress[key] for key in ('candidate_number', 'candidate_total')
                         if key in self.progress and result.get('candidate') is not None and
                         result['candidate'] == self.progress.get('candidate')}
            self.progress = dict(candidate, **result)
        else:
            self.progress.update(result)
        self.progress['elapsed_seconds'] = time.monotonic() - self.operation_started
        self.progress['server'] = self.preset['label'] if self.preset is not None else self.address
        self.notify(dict(self.progress))

    def wait_ready(self, poll=None, require_address=True):
        self.update({'activity': 'reconnecting', 'recovery_deadline': time.monotonic() + 20})
        try:
            self.adapter.wait_ready(self.cancel, poll=poll, require_address=require_address)
        finally:
            self.update({'activity': None, 'recovery_deadline': None})

    def check_budget(self, seconds, tests=1):
        max_seconds = MAX_LAN_TRAFFIC_SECONDS
        max_bytes = MAX_LAN_TRAFFIC_BYTES
        reserved = 1_000_000_000 * (seconds + 2 * tests) // 8
        if (self.traffic_seconds + seconds > max_seconds or
                self.traffic_bytes + reserved > max_bytes or
                time.monotonic() + seconds + 8 * tests + 20 > self.deadline):
            raise CalibrationError('Calibration inconclusive within traffic/time limits; '
                                   'use a wired LAN server for extended verification')
        return reserved

    def keepalive(self):
        while not self.lease_stop.wait(1):
            try:
                self.adapter.write('keepalive')
            except (OSError, CalibrationError) as error:
                self.lease_error = error
                self.cancel.set()
                return

    def test(self, reverse, seconds, source):
        # Reserve at gigabit line rate before starting, not after exceeding the cap.
        reserved = self.check_budget(seconds)
        self.traffic_seconds += seconds
        self.traffic_bytes += reserved
        if not self.adapter.link_ready() or self.adapter.address() != source:
            raise CalibrationError('Selected onboard link/address changed')
        route = run_command(['ip', 'route', 'get', self.address, 'from', source,
                             'oif', self.adapter.name])
        if not onboard_route(route, self.adapter.name):
            raise CalibrationError('Server route does not use the onboard adapter')
        before = self.adapter.counters()
        arguments = ['iperf3', '-c', self.address, '-p', str(self.port),
                     '--bind-dev', self.adapter.name, '-B', source, '-J',
                     '--connect-timeout', '3000', '-t', str(seconds)]
        if reverse:
            arguments.append('-R')
        started = time.monotonic()
        def poll():
            self.update({'activity': 'traffic', 'traffic_seconds': time.monotonic() - started,
                         'traffic_target_seconds': seconds})
            if self.scan_probe:
                self.reject_corruption(before)
        try:
            output = run_command(arguments, seconds + 8, self.cancel, allow_failure=True, poll=poll)
        finally:
            self.update({'activity': None})
        after = self.adapter.counters()
        if not self.adapter.link_ready() or self.adapter.address() != source:
            raise CalibrationError('Selected onboard link/address changed')
        try:
            result = json.loads(output)
        except ValueError as error:
            raise CalibrationError('Malformed iperf3 output') from error
        if isinstance(result, dict) and result.get('error'):
            message = str(result['error']).lower()
            if 'timed out' in message or 'timeout' in message:
                raise CandidateError('Traffic timed out at this timing candidate')
            raise CalibrationError('Traffic server refused or could not complete the test; no retries')
        evidence = verify_measurement(result, before, after, reverse, seconds)
        self.last_measurement = {'before': before, 'after': after,
                                 'ended': time.monotonic(), 'evidence': evidence}
        return evidence

    def reject_corruption(self, before):
        delta = counter_delta(before, self.adapter.counters())
        if any(value for key, value in delta.items() if key.startswith('crc:')):
            raise CRCError('CRC errors detected; skipping this setting')

    def run(self):
        if self.public:
            raise CalibrationError('Full calibration and saving require a wired LAN server')
        lease = None
        accepted = False
        try:
            self.wait_ready()
            source = self.adapter.address()
            self.store.load()
            if self.address in [item.address() for item in adapters()]:
                raise CalibrationError('The test server must be another machine')
            route = run_command(['ip', 'route', 'get', self.address, 'from', source,
                                 'oif', self.adapter.name])
            if not onboard_route(route, self.adapter.name):
                raise CalibrationError('Server route does not leave the onboard adapter')
            capabilities = self.adapter.capabilities()
            original = capabilities['index']
            if original < 0:
                original = capabilities['default_index']
            self.adapter.write('begin')
            lease = threading.Thread(target=self.keepalive, daemon=True)
            lease.start()
            clean = set()
            self.scan_probe = True
            for index in range(32):
                if self.cancel.is_set():
                    raise Cancelled('Calibration cancelled')
                self.update({'phase': 'scanning', 'candidate': index,
                             'candidate_number': index + 1, 'candidate_total': 32,
                             'activity': 'applying'})
                self.adapter.write('set ' + str(index))
                try:
                    baseline = self.adapter.counters()
                    self.wait_ready(poll=lambda: self.reject_corruption(baseline))
                    source = self.adapter.address()
                    self.test(True, 2, source)
                    clean.add(index)
                except Cancelled:
                    raise
                except CandidateError as error:
                    self.update({'phase': 'scanning', 'candidate': index,
                                 'candidate_error': str(error)})
                    # Measurement failures are candidate evidence, never saved health.
            self.scan_probe = False
            for attempt in range(MAX_VERIFICATION_ATTEMPTS):
                chosen, window = choose_window(clean, original)
                repetitions = 2 if window[1] - window[0] < 2 else 1
                self.check_budget(60 * repetitions, 2 * repetitions)
                self.adapter.write('set ' + str(chosen))
                rounds = []
                try:
                    self.wait_ready()
                    source = self.adapter.address()
                    for repetition in range(repetitions):
                        # Reopen between narrow-window rounds to verify reapplication too.
                        if repetition:
                            self.adapter.write('set ' + str(chosen))
                            self.wait_ready()
                            source = self.adapter.address()
                        measurements = {}
                        for direction, reverse in (('send', False), ('receive', True)):
                            self.update({'phase': 'verifying_' + direction, 'candidate': chosen,
                                         'verification_round': repetition + 1,
                                         'verification_rounds': repetitions})
                            measurements[direction] = self.test(reverse, 30, source)
                        rounds.append(measurements)
                    break
                except Cancelled:
                    raise
                except CandidateError as error:
                    clean.remove(chosen)
                    self.update({'phase': 'verification_failed', 'candidate': chosen,
                                 'candidate_error': str(error)})
                    if attempt + 1 == MAX_VERIFICATION_ATTEMPTS or not clean:
                        raise CalibrationError('No fully verified timing setting within the '
                                               'bounded attempts; previous calibration retained') from error
            if self.lease_error is not None:
                raise CalibrationError('Driver rollback lease failed')
            profile = {'schema': 1, 'identity': self.adapter.identity, 'index': chosen,
                       'window': window, 'verified_at': int(time.time()),
                       'verification': dict(rounds[-1], rounds=rounds)}
            self.update({'phase': 'saving', 'candidate': chosen})
            # Stop renewal before accept so a final keepalive cannot race acceptance.
            self.lease_stop.set()
            lease.join()

            def accept():
                if self.cancel.is_set():
                    raise Cancelled('Calibration cancelled before saving')
                self.adapter.write('accept')

            self.store.save(profile, self.adapter.identity, accept)
            accepted = True
            self.update({'phase': 'complete', 'index': chosen, 'window': window})
        finally:
            self.scan_probe = False
            self.lease_stop.set()
            if lease is not None:
                lease.join()
                if not accepted and self.adapter.capabilities()['lease']:
                    self.adapter.write('restore')


class DiagnosticReceiver:
    """One bounded iperf3 TCP receive stream; no server-controlled read sizes."""

    def __init__(self, job, source):
        self.job = job
        self.source = source
        self.received = 0
        self.started = None
        self.control_bytes = 0

    def connect(self):
        stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                              self.job.adapter.name.encode() + b'\0')
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
            stream.settimeout(3)
            stream.bind((self.source, 0))
            stream.connect((self.job.address, self.job.port))
            return stream
        except OSError as error:
            stream.close()
            raise Inconclusive('Cannot reach the traffic server; it may be unavailable') from error

    def check(self):
        if getattr(self.job, 'lease_error', None) is not None:
            raise CalibrationError('Driver rollback lease failed')
        if self.job.cancel.is_set():
            raise Cancelled('Ethernet error check cancelled')
        self.job.inspect()

    def read(self, stream, size, deadline):
        if size > 64 * 1024 or self.control_bytes + size > 512 * 1024:
            raise Inconclusive('Traffic server returned excessive control data')
        data = bytearray()
        while len(data) < size:
            self.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Inconclusive('Traffic server timed out')
            stream.settimeout(min(.2, remaining))
            try:
                chunk = stream.recv(size - len(data))
            except socket.timeout:
                continue
            if not chunk:
                raise Inconclusive('Traffic server closed the connection')
            data.extend(chunk)
        self.control_bytes += len(data)
        return bytes(data)

    def expect(self, control, state):
        actual = struct.unpack('b', self.read(control, 1, time.monotonic() + 5))[0]
        if actual == -1:
            raise Inconclusive('Server is busy or refused the test; try later or choose another server')
        if actual != state:
            raise Inconclusive('Traffic server refused or uses an unsupported protocol')

    @staticmethod
    def send_json(control, value):
        encoded = json.dumps(value).encode()
        control.sendall(struct.pack('!I', len(encoded)) + encoded)

    def run(self):
        cookie = secrets.token_hex(18).encode() + b'\0'
        with self.connect() as control:
            control.sendall(cookie)
            self.expect(control, 9)  # PARAM_EXCHANGE
            self.send_json(control, {'tcp': True, 'parallel': 1, 'reverse': True,
                                     'time': self.job.attempt_seconds, 'num': self.job.attempt_payload,
                                     'len': 64 * 1024, 'bandwidth': PUBLIC_RATE, 'omit': 0,
                                     'client_version': 'CoreELEC error check'})
            self.expect(control, 10)  # CREATE_STREAMS
            with self.connect() as data:
                data.sendall(cookie)
                self.expect(control, 1)  # TEST_START
                self.expect(control, 2)  # TEST_RUNNING
                self.started = time.monotonic()
                deadline = self.started + self.job.attempt_seconds
                exchanged = False
                while self.received < min(self.job.attempt_payload, PUBLIC_MAX_BYTES):
                    self.check()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    ready, _, _ = select.select([control, data], [], [], min(.2, remaining))
                    if control in ready:
                        self.expect(control, 13)  # EXCHANGE_RESULTS
                        exchanged = True
                        break
                    if data in ready:
                        chunk = data.recv(min(64 * 1024, self.job.attempt_payload - self.received,
                                              PUBLIC_MAX_BYTES - self.received))
                        if not chunk:
                            break
                        self.received += len(chunk)
                duration = time.monotonic() - self.started
                if not exchanged:
                    control.sendall(b'\x04')  # TEST_END
                    self.expect(control, 13)
                self.send_json(control, {
                    'cpu_util_total': 0, 'cpu_util_user': 0, 'cpu_util_system': 0,
                    'sender_has_retransmits': -1,
                    'streams': [{'id': 1, 'bytes': self.received, 'retransmits': -1,
                                 'jitter': 0, 'errors': 0, 'packets': 0,
                                 'start_time': 0, 'end_time': duration}]})
                size = struct.unpack('!I', self.read(control, 4, time.monotonic() + 5))[0]
                response = json.loads(self.read(control, size, time.monotonic() + 5))
                if not isinstance(response, dict) or not isinstance(response.get('streams'), list):
                    raise CalibrationError('Error-check server returned invalid results')
                self.expect(control, 14)  # DISPLAY_RESULTS
                control.sendall(b'\x10')  # IPERF_DONE
                return duration


class DiagnosticJob(Job):
    attempt_seconds = PUBLIC_SECONDS
    attempt_payload = PUBLIC_PAYLOAD

    def receiver(self, source):
        if self.backend == HTTPS_BACKEND:
            from https_receive import HTTPSReceiver
            return HTTPSReceiver(self, source)
        return DiagnosticReceiver(self, source)

    def route(self):
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            raise CalibrationError('Public traffic deadline reached before route check')
        return run_command(['ip', 'route', 'get', self.address, 'from', self.source,
                            'oif', self.adapter.name], timeout=min(5, remaining), cancel=self.cancel)

    def inspect(self):
        now = time.monotonic()
        if now >= self.deadline:
            raise CalibrationError('Public traffic deadline reached; original setting retained')
        if now - self.sampled < .2:
            return
        self.sampled = now
        receiver = getattr(self, 'current_receiver', None)
        if receiver is not None:
            self.update({'activity': 'receiving' if receiver.started is not None else 'connecting',
                         'received_bytes': receiver.received, 'target_bytes': self.attempt_payload,
                         'traffic_seconds': now - receiver.started if receiver.started is not None else 0,
                         'traffic_target_seconds': self.attempt_seconds})
        if self.lease_error is not None:
            raise CalibrationError('Driver rollback lease failed')
        self.reject_corruption(self.before)
        if not self.adapter.link_ready() or self.adapter.address() != self.source:
            raise CalibrationError('Selected onboard link/address changed')

    def prepare(self):
        if self.preset is not None:
            self.update({'phase': 'resolving', 'server': self.preset['label']})
            self.address = resolve_public_server(self.preset, self.cancel, self.deadline)
        self.source = self.adapter.address()
        if self.address in [item.address() for item in adapters()]:
            raise CalibrationError('The test server must be another machine')
        if not self.adapter.link_ready():
            raise CalibrationError('Onboard Ethernet link is unavailable')
        route = self.route()
        if not onboard_route(route, self.adapter.name):
            raise CalibrationError('Server route does not use the onboard adapter')

    def measure(self, seconds=PUBLIC_SECONDS):
        self.attempt_seconds = seconds
        self.attempt_payload = PUBLIC_RATE * seconds // 8
        self.source = self.adapter.address()
        route = self.route()
        if not onboard_route(route, self.adapter.name):
            raise CalibrationError('Server route does not use the onboard adapter')
        self.before = self.adapter.counters()
        self.sampled = time.monotonic()
        started = self.sampled
        self.started = started
        receiver = self.receiver(self.source)
        self.current_receiver = receiver
        try:
            duration = receiver.run()
        except CRCError:
            duration = time.monotonic() - (receiver.started or started)
        finally:
            self.traffic_bytes += receiver.received
            self.current_receiver = None
            self.update({'activity': None})
        after = self.adapter.counters()
        delta = counter_delta(self.before, after)
        if not self.adapter.link_ready() or self.adapter.address() != self.source:
            raise CalibrationError('Selected onboard link/address changed')
        errors = max((value for key, value in delta.items() if key.startswith('crc:')), default=0)
        if delta['rx_bytes'] < receiver.received * .9:
            raise CalibrationError('Error-check traffic did not use the onboard adapter')
        rate = receiver.received * 8 / max(duration, .001)
        state = ('corruption' if errors else 'no_errors' if (
            seconds * .9 <= duration <= seconds * 1.05 and
            receiver.received >= self.attempt_payload * .8 and
            PUBLIC_RATE * .8 <= rate <= PUBLIC_RATE * 1.2) else 'inconclusive')
        transport = getattr(receiver, 'evidence', None)
        if self.backend == HTTPS_BACKEND and state == 'no_errors':
            if (transport is None or
                    not seconds * .9 <= transport['tcp_progress_seconds'] <= seconds * 1.05 or
                    transport['tcp_unique_received_bytes'] < receiver.received * .9):
                state = 'inconclusive'
        self.last_measurement = {'before': self.before, 'after': after, 'started': started,
                                 'ended': time.monotonic(), 'evidence': {'seconds': duration}}
        if transport is not None:
            self.last_measurement['transport'] = transport
        result = {
            'state': state, 'bytes': receiver.received, 'seconds': duration,
            'rate_bps': rate, 'crc_delta': errors, 'gigabit_verified': False, 'saved': False,
            'address': self.address, 'port': self.port,
            'preset': self.preset['id'] if self.preset else None}
        if self.backend == HTTPS_BACKEND:
            result.update(traffic_backend=self.backend, endpoint=HTTPS_ENDPOINT,
                          rate_semantics='application_payload', pacing='application_read',
                          wire_rate_limited=False, wire_bytes_capped=False)
        return result

    def run(self):
        self.prepare()
        self.update({'phase': 'diagnosing'})
        result = self.measure()
        self.update({'phase': 'diagnostic_complete', 'diagnostic': result})


class PublicRepairJob(DiagnosticJob):
    """CRC-driven low-rate repair; network failures never reject a timing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deadline = time.monotonic() + PUBLIC_REPAIR_MAX_SECONDS
        self.reserved_bytes = 0

    def reserve(self, seconds, attempts=1):
        payload = PUBLIC_RATE * seconds // 8
        if (self.reserved_bytes + payload > PUBLIC_REPAIR_MAX_BYTES or
                time.monotonic() + seconds + 30 * attempts > self.deadline):
            raise Inconclusive('Public repair limit reached; original Ethernet setting retained')
        self.reserved_bytes += payload

    def ready(self, index):
        before = self.adapter.counters()

        def poll():
            if time.monotonic() >= self.deadline:
                raise CalibrationError('Public traffic deadline reached; original setting retained')
            if self.lease_error is not None:
                raise CalibrationError('Driver rollback lease failed')
            cap = self.adapter.capabilities()
            if (cap['index'] != index or not cap['lease'] or not cap['applied'] or
                    cap['error'] or cap['generation'] != before['generation']):
                raise CalibrationError('Ethernet timing changed during public recovery')
            self.reject_corruption(before)

        try:
            self.wait_ready(poll=poll)
        except CRCError:
            raise
        except CandidateError as error:
            raise RecoveryTimeout('Ethernet did not recover at this timing') from error
        found = adapters()
        cap = self.adapter.capabilities()
        if (len(found) != 1 or found[0].identity != self.adapter.identity or
                cap['index'] != index or not cap['lease'] or not cap['applied'] or cap['error']):
            raise CalibrationError('Ethernet timing/identity changed during public repair')

    def candidates(self, original):
        preferred = []
        previous = self.store.load('previous')
        if previous is not None:
            try:
                index = validate_profile(previous, self.adapter.identity)
            except CalibrationError as error:
                logging.getLogger('ethernet-calibration').warning(
                    'Previous calibration cannot provide a timing hint: %s', error)
            else:
                if index != original:
                    preferred.append(index)
        return preferred + [index for index in range(32)
                            if index != original and index not in preferred]

    def recover_start(self, lease, initial_cap, index, error):
        self.update({'phase': 'public_recovering', 'candidate': index,
                     'candidate_inconclusive': str(error)})
        self.lease_stop.set()
        lease.join()
        if self.lease_error is not None:
            raise CalibrationError('Driver rollback lease failed')
        self.adapter.write('restore')
        self.reserve(0)
        restored = self.adapter.capabilities()

        def poll():
            if time.monotonic() >= self.deadline:
                raise CalibrationError('Public traffic deadline reached; original setting retained')
            cap = self.adapter.capabilities()
            if (cap['index'] != initial_cap['index'] or cap['lease'] or not cap['applied'] or
                    cap['error'] or cap['generation'] != restored['generation']):
                raise CalibrationError('Original Ethernet state changed during recovery')

        try:
            self.wait_ready(poll=poll)
        except CandidateError as recovery_error:
            raise CalibrationError('Original Ethernet link/address did not recover; repair stopped') from recovery_error
        poll()
        found = adapters()
        if len(found) != 1 or found[0].identity != self.adapter.identity:
            raise CalibrationError('Ethernet identity changed during recovery')
        self.source = self.adapter.address()
        route = self.route()
        if not onboard_route(route, self.adapter.name):
            raise CalibrationError('Original onboard route did not recover')
        self.adapter.write('begin')
        cap = self.adapter.capabilities()
        if (cap['index'] != initial_cap['index'] or not cap['lease'] or not cap['applied'] or
                cap['error'] or cap['generation'] != restored['generation']):
            raise CalibrationError('Original Ethernet state could not be leased again')
        self.lease_stop.clear()
        lease = threading.Thread(target=self.keepalive, daemon=True)
        lease.start()
        return lease

    def verified_receive(self, index):
        result = self.measure()
        if result['state'] == 'corruption':
            raise CRCError('CRC errors during public verification')
        if result['state'] != 'no_errors':
            raise Inconclusive('Too little clean public traffic; repair is inconclusive')
        cap = self.adapter.capabilities()
        observation = self.last_measurement
        if cap['index'] != index or cap['generation'] != observation['after']['generation']:
            raise CalibrationError('Ethernet timing changed during public verification')
        evidence = {'bps': result['rate_bps'], 'bytes': result['bytes'], 'seconds': result['seconds'],
                'crc_delta': 0, 'rx_bytes': observation['after']['rx_bytes'] - observation['before']['rx_bytes'],
                'source': self.source, 'interface': self.adapter.name, 'index': index,
                'generation': cap['generation']}
        if self.backend == HTTPS_BACKEND:
            evidence['application_bps'] = evidence.pop('bps')
            evidence.update(observation['transport'])
        return evidence

    def run(self):
        lease = None
        accepted = False
        try:
            self.store.load()
            self.store.load('previous')
            self.prepare()
            self.reserve(PUBLIC_SECONDS)
            initial_cap = self.adapter.capabilities()
            self.update({'phase': 'diagnosing'})
            initial = self.measure()
            if initial['state'] != 'corruption':
                self.update({'phase': 'diagnostic_complete', 'diagnostic': initial})
                return
            cap = self.adapter.capabilities()
            if (cap['index'] != initial_cap['index'] or cap['lease'] or
                    cap['generation'] != self.last_measurement['after']['generation'] or
                    initial_cap['generation'] != self.last_measurement['before']['generation']):
                raise CalibrationError('Ethernet changed since the observed CRC errors')
            self.adapter.write('begin')
            lease = threading.Thread(target=self.keepalive, daemon=True)
            lease.start()
            original = self.adapter.capabilities()['index']
            candidates = self.candidates(original)
            for position, index in enumerate(candidates, 1):
                if self.cancel.is_set():
                    raise Cancelled('Public repair cancelled')
                self.reserve(3)
                self.update({'phase': 'public_scanning', 'candidate': index,
                             'candidate_number': position, 'candidate_total': len(candidates),
                             'activity': 'applying',
                             'reserved_bytes': self.reserved_bytes,
                             'payload_limit': PUBLIC_REPAIR_MAX_BYTES})
                self.adapter.write('set ' + str(index))
                try:
                    self.ready(index)
                except CRCError:
                    continue
                except RecoveryTimeout as error:
                    lease = self.recover_start(lease, initial_cap, index, error)
                    continue
                screen = self.measure(3)
                if screen['state'] == 'corruption':
                    continue
                if screen['state'] != 'no_errors':
                    raise Inconclusive('Public server traffic is inconclusive; original setting retained')
                self.reserve(2 * PUBLIC_SECONDS, 2)
                rounds = []
                try:
                    for repetition in range(2):
                        self.update({'phase': 'public_verifying', 'candidate': index,
                                     'verification_round': repetition + 1, 'verification_rounds': 2,
                                     'activity': 'applying'})
                        self.adapter.write('set ' + str(index))
                        self.ready(index)
                        rounds.append(self.verified_receive(index))
                except CRCError:
                    continue
                except RecoveryTimeout as error:
                    lease = self.recover_start(lease, initial_cap, index, error)
                    continue
                if self.lease_error is not None:
                    raise CalibrationError('Driver rollback lease failed')
                if self.cancel.is_set():
                    raise Cancelled('Public repair cancelled before saving')
                cap = self.adapter.capabilities()
                if (not cap['lease'] or not cap['applied'] or cap['error'] or
                        cap['index'] != index or cap['generation'] != rounds[-1]['generation'] or
                        not self.adapter.link_ready() or self.adapter.address() != rounds[-1]['source']):
                    raise CalibrationError('Ethernet changed before saving public repair')
                profile = {'schema': 2, 'kind': 'public_error_checked',
                           'identity': self.adapter.identity, 'index': index,
                           'verified_at': int(time.time()), 'verification': {'rounds': rounds}}
                if self.backend == HTTPS_BACKEND:
                    profile.update(schema=4, traffic_backend=HTTPS_BACKEND)
                self.update({'phase': 'saving', 'candidate': index})
                self.lease_stop.set()
                lease.join()

                def accept():
                    if self.cancel.is_set():
                        raise Cancelled('Public repair cancelled before saving')
                    self.adapter.write('accept')

                self.store.save(profile, self.adapter.identity, accept)
                accepted = True
                self.update({'phase': 'public_complete', 'index': index,
                             'kind': 'public_error_checked', 'gigabit_verified': False,
                             'traffic_backend': self.backend,
                             'received_bytes': self.traffic_bytes, 'reserved_bytes': self.reserved_bytes})
                return
            raise CRCError('No timing passed repeated packet checks; original setting retained')
        finally:
            self.lease_stop.set()
            if lease is not None:
                lease.join()
                if not accepted and self.adapter.capabilities()['lease']:
                    self.adapter.write('restore')


class Health:
    def __init__(self):
        self.baseline = None
        self.state = {'state': 'unknown'}
        self.identity = None

    def reset(self):
        self.baseline = None
        self.state = {'state': 'unknown'}
        self.identity = None

    def verified(self, adapter, observation):
        """Seed recent health from the just-completed, verified receive leg."""
        self.reset()
        self.identity = adapter.identity
        self.baseline = observation['ended'], observation['after']
        delta = counter_delta(observation['before'], observation['after'])
        self.state = {'state': 'healthy', 'window_seconds': observation['evidence']['seconds'],
                      'crc_delta': 0, 'rx_bytes': delta['rx_bytes'],
                      'measured_at': int(time.time())}

    def sample(self, adapter, now):
        if self.identity is not None and self.identity != adapter.identity:
            self.reset()
        self.identity = adapter.identity
        if not adapter.link_ready():
            self.reset()
            self.state = {'state': 'link_down'}
            return
        counters = adapter.counters()
        if self.baseline is None:
            self.baseline = now, counters
            if self.state['state'] == 'link_down':
                self.state = {'state': 'unknown'}
            return
        started, previous = self.baseline
        try:
            delta = counter_delta(previous, counters)
        except CalibrationError:
            self.reset()
            self.baseline = now, counters
            return
        errors = max((value for key, value in delta.items() if key.startswith('crc:')), default=0)
        if errors:
            self.state = {'state': 'corruption', 'window_seconds': now - started,
                          'crc_delta': errors, 'rx_bytes': delta['rx_bytes'],
                          'measured_at': int(time.time())}
        if now - started >= 60:
            state = 'corruption' if errors else (
                'healthy' if delta['rx_bytes'] >= MIN_HEALTH_BYTES else 'insufficient_traffic')
            self.state = {'state': state, 'window_seconds': now - started,
                          'crc_delta': errors, 'rx_bytes': delta['rx_bytes'],
                          'measured_at': int(time.time())}
            self.baseline = now, counters
