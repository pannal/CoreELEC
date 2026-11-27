# SPDX-License-Identifier: GPL-2.0-or-later
"""Selected experimental HTTPS payload, sharing the public job's safety gates."""
import hashlib
import errno
import re
import select
import socket
import ssl
import struct
import time

from calibration import CalibrationError, DiagnosticReceiver, HTTPS_BACKEND, HTTPS_ENDPOINT, Inconclusive, PUBLIC_MAX_BYTES, PUBLIC_RATE

TLS_HOST = 'speed.cloudflare.com'


def tls_context():
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class HTTPSReceiver(DiagnosticReceiver):
    def __init__(self, job, source):
        if (type(job.attempt_payload) is not int or not 0 < job.attempt_payload <= PUBLIC_MAX_BYTES or
                type(job.attempt_seconds) is not int or not 0 < job.attempt_seconds <= 20):
            raise CalibrationError('HTTPS attempt exceeds payload/duration limits')
        super().__init__(job, source)
        self.evidence = None
        self.stream = None
        self.deadline = min(job.deadline, time.monotonic() + job.attempt_seconds + 8)
        self.last_data = time.monotonic()
        self.tcp_initial = None
        self.tcp_last = None
        self.tcp_progress_seconds = 0

    def connect(self):
        stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                              self.job.adapter.name.encode()+b'\0')
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32768)
            stream.bind((self.source, 0))
            stream.setblocking(False)
            code = stream.connect_ex((self.job.address, self.job.port))
            if code not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK):
                raise OSError(code, 'HTTPS TCP connect failed')
            connect_deadline = min(self.deadline, time.monotonic()+3)
            while code:
                self.check()
                if time.monotonic() >= connect_deadline:
                    raise Inconclusive('HTTPS connection timed out')
                if select.select([], [stream], [], .05)[1]:
                    code = stream.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if code:
                        raise OSError(code, 'HTTPS TCP connect failed')
                    break
            return stream
        except OSError as error:
            stream.close()
            raise Inconclusive('HTTPS connection failed: %s' % error) from error
        except CalibrationError:
            stream.close()
            raise

    def tcp_received(self):
        info = self.stream.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 256)
        # Linux 4.9+ tcp_info: bytes_received follows pacing rates and bytes_acked.
        if len(info) < 136:
            raise CalibrationError('Kernel cannot provide HTTPS transport-byte evidence')
        return struct.unpack_from('=Q', info, 128)[0]

    def check(self):
        super().check()
        now = time.monotonic()
        if now >= self.deadline:
            raise Inconclusive('HTTPS traffic attempt timed out')
        if self.tcp_initial is not None:
            value = self.tcp_received()
            if value < self.tcp_last:
                raise CalibrationError('HTTPS transport counters reset')
            if value > self.tcp_last:
                self.tcp_progress_seconds = now - self.started
            self.tcp_last = value

    def wait(self, writing=False):
        self.check()
        select.select([] if writing else [self.stream],
                      [self.stream] if writing else [], [], .05)

    def receive(self, count):
        while True:
            self.check()
            try:
                data = self.stream.recv(count)
                if data:
                    self.last_data = time.monotonic()
                return data
            except ssl.SSLWantReadError:
                if time.monotonic() - self.last_data >= 4:
                    raise Inconclusive('HTTPS server stalled; test is inconclusive')
                self.wait()
            except ssl.SSLWantWriteError:
                self.wait(True)

    def exact(self, count, payload=False):
        data = bytearray()
        while len(data) < count:
            chunk = self.receive(min(16384, count-len(data)))
            if not chunk:
                raise Inconclusive('HTTPS response was truncated')
            data.extend(chunk)
            if payload:
                self.received += len(chunk)
            else:
                self.control_bytes += len(chunk)
                if self.control_bytes > 512*1024:
                    raise Inconclusive('HTTPS framing exceeded control-data limit')
        return bytes(data)

    def line(self, limit=4096):
        data = bytearray()
        while not data.endswith(b'\r\n'):
            if len(data) >= limit:
                raise Inconclusive('HTTPS header/framing line is too large')
            data.extend(self.exact(1))
        return bytes(data[:-2])

    @staticmethod
    def header(line):
        key, value = line.split(b':', 1)
        if re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) is None:
            raise Inconclusive('Invalid HTTPS header name')
        return key.decode('ascii').lower(), value.decode('iso-8859-1').strip()

    def send(self, data):
        while data:
            self.check()
            try:
                count = self.stream.send(data)
                if not count:
                    raise Inconclusive('HTTPS request could not be sent')
                data = data[count:]
            except ssl.SSLWantWriteError:
                self.wait(True)
            except ssl.SSLWantReadError:
                self.wait()

    def run(self):
        if self.job.backend != HTTPS_BACKEND:
            raise CalibrationError('Unsupported HTTPS source')
        interface_index = socket.if_nametoindex(self.job.adapter.name)
        raw = self.connect()
        try:
            raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32768)
            context = tls_context()
            if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
                raise CalibrationError('HTTPS certificate verification cannot be bypassed')
            self.stream = context.wrap_socket(raw, server_hostname=TLS_HOST,
                                              do_handshake_on_connect=False)
            self.stream.setblocking(False)
            with self.stream:
                actual_buffer = self.stream.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
                if actual_buffer > 128*1024:
                    raise CalibrationError('HTTPS receive buffering exceeds limit')
                while True:
                    self.check()
                    try:
                        self.stream.do_handshake()
                        break
                    except ssl.SSLWantReadError:
                        self.wait()
                    except ssl.SSLWantWriteError:
                        self.wait(True)
                self.send(('GET /__down?bytes=%d HTTP/1.1\r\nHost: %s\r\n'
                           'Accept-Encoding: identity\r\nCache-Control: no-cache\r\n'
                           'User-Agent: CoreELEC-experimental-error-check\r\n'
                           'Connection: close\r\n\r\n' %
                           (self.job.attempt_payload, TLS_HOST)).encode('ascii'))
                status = self.line(1024).decode('ascii').split(' ', 2)
                if len(status) < 2 or status[0] not in ('HTTP/1.0', 'HTTP/1.1'):
                    raise Inconclusive('Unsupported HTTPS response status')
                code = int(status[1])
                headers = {}
                while True:
                    line = self.line()
                    if self.control_bytes > 16384:
                        raise Inconclusive('HTTPS response headers exceed limit')
                    if not line:
                        break
                    key, value = self.header(line)
                    if key in headers and key in ('content-length', 'transfer-encoding', 'location',
                                                   'content-type', 'content-encoding'):
                        raise Inconclusive('Duplicate HTTPS framing header')
                    headers[key] = value
                if code != 200:
                    if code in (301, 302, 303, 307, 308):
                        raise Inconclusive('HTTPS redirect refused; no alternate source tried')
                    raise Inconclusive('HTTPS server refused/status %d; no retries' % code)
                if headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise Inconclusive('Compressed HTTPS test payload refused')
                if headers.get('content-type', '').split(';')[0].strip() != 'application/octet-stream':
                    raise Inconclusive('HTTPS response is not traffic-test payload')
                chunked = headers.get('transfer-encoding', '').lower() == 'chunked'
                if 'transfer-encoding' in headers and not chunked:
                    raise Inconclusive('Unsupported HTTPS transfer encoding')
                if chunked and 'content-length' in headers:
                    raise Inconclusive('Ambiguous HTTPS transfer framing')
                if not chunked and headers.get('content-length') != str(self.job.attempt_payload):
                    raise Inconclusive('HTTPS payload length does not match request')
                self.started = time.monotonic()
                self.tcp_initial = self.tcp_last = self.tcp_received()
                remaining_chunk = 0
                while self.received < self.job.attempt_payload:
                    if chunked and not remaining_chunk:
                        size = self.line(1024)
                        if not size or any(c not in b'0123456789abcdefABCDEF' for c in size):
                            raise Inconclusive('Invalid HTTPS chunk size')
                        remaining_chunk = int(size, 16)
                        if not remaining_chunk or remaining_chunk > self.job.attempt_payload-self.received:
                            raise Inconclusive('Short or oversized HTTPS chunked payload')
                    count = min(16384, self.job.attempt_payload-self.received,
                                remaining_chunk if chunked else self.job.attempt_payload)
                    self.exact(count, payload=True)
                    if chunked:
                        remaining_chunk -= count
                        if not remaining_chunk and self.exact(2) != b'\r\n':
                            raise Inconclusive('Invalid HTTPS chunk delimiter')
                    paced_until = self.started + self.received*8/PUBLIC_RATE
                    while time.monotonic() < paced_until:
                        self.check()
                        self.job.cancel.wait(min(.05, paced_until-time.monotonic()))
                    self.last_data = time.monotonic()
                if chunked:
                    if self.line(1024) != b'0':
                        raise Inconclusive('HTTPS sent extra payload')
                    trailers = self.control_bytes
                    while True:
                        line = self.line()
                        if not line:
                            break
                        if self.control_bytes-trailers > 4096:
                            raise Inconclusive('HTTPS trailers exceed limit')
                        key, _ = self.header(line)
                        if key in ('content-length', 'transfer-encoding', 'content-type',
                                   'content-encoding', 'location'):
                            raise Inconclusive('HTTPS framing trailers refused')
                self.check()
                if (socket.if_nametoindex(self.job.adapter.name) != interface_index or
                        self.stream.getsockname()[0] != self.source or
                        self.stream.getpeername() != (self.job.address, self.job.port)):
                    raise CalibrationError('HTTPS interface/source/peer changed')
                self.evidence = {
                    'backend': HTTPS_BACKEND, 'endpoint': HTTPS_ENDPOINT,
                    'tls_verified': True, 'tls_version': self.stream.version(),
                    'certificate_sha256': hashlib.sha256(
                        self.stream.getpeercert(binary_form=True)).hexdigest(),
                    'http_status': code, 'requested_bytes': self.job.attempt_payload,
                    'framing': 'chunked' if chunked else 'content_length',
                    'declared_bytes': None if chunked else self.job.attempt_payload,
                    'pacing': 'application_read', 'pacing_bps': PUBLIC_RATE,
                    'wire_rate_limited': False, 'wire_bytes_capped': False,
                    'tcp_unique_received_bytes': self.tcp_last-self.tcp_initial,
                    'tcp_progress_seconds': self.tcp_progress_seconds}
                return time.monotonic()-self.started
        except (OSError, ValueError, KeyError) as error:
            raise Inconclusive('HTTPS traffic failed: %s' % error) from error
        finally:
            raw.close()
