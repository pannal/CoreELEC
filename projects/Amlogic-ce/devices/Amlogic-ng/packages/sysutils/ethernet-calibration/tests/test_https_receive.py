# SPDX-License-Identifier: GPL-2.0-or-later
"""Real local TLS/HTTP fixtures; no public servers or hardware are contacted."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'source'))
import calibration as cal
import https_receive as transport


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.requests.append(self.path)
        mode = self.server.mode
        count = int(self.path.split('bytes=')[1])
        try:
            if mode.startswith('status'):
                self.send_response(int(mode[6:]))
                self.end_headers()
                return
            if mode.startswith('redirect'):
                self.send_response(302)
                self.send_header('Location', 'https://localhost/elsewhere' if mode == 'redirect_same'
                                 else 'https://127.0.0.1/elsewhere')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            chunked = mode in ('chunked', 'oversized_chunk', 'bad_chunk', 'extra_chunk', 'ambiguous')
            if chunked:
                self.send_header('Transfer-Encoding', 'chunked')
            if not chunked or mode == 'ambiguous':
                self.send_header('Content-Length', str(count+1 if mode == 'wrong_length' else count))
            if mode == 'duplicate':
                self.send_header('Content-Length', str(count))
            if mode == 'gzip':
                self.send_header('Content-Encoding', 'gzip')
            if mode == 'large_header':
                self.send_header('X-Oversized', 'x'*17000)
            self.end_headers()
            if mode == 'stall':
                self.server.stop.wait(6)
                return
            if mode == 'truncated':
                self.wfile.write(b'x'*1024)
                return
            if mode == 'oversized_chunk':
                self.wfile.write(('%x\r\n' % (count+1)).encode())
                return
            if mode == 'bad_chunk':
                self.wfile.write(b'not-hex\r\n')
                return
            remaining = count
            while remaining and not self.server.stop.is_set():
                size = min(8192, remaining)
                if chunked:
                    self.wfile.write(('%x\r\n' % size).encode())
                self.wfile.write(b'x'*size)
                if chunked:
                    self.wfile.write(b'\r\n')
                remaining -= size
            if chunked:
                self.wfile.write(b'1\r\nx\r\n0\r\n\r\n' if mode == 'extra_chunk' else b'0\r\n\r\n')
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            self.server.aborted = True


class HTTPSFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.cert, cls.key = root/'cert.pem', root/'key.pem'
        environment = dict(os.environ)
        for key in ('LD_LIBRARY_PATH', 'PYTHONHOME'):
            environment.pop(key, None)
        result = subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                                 '-days', '1', '-subj', '/CN=localhost',
                                 '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
                                 '-keyout', str(cls.key), '-out', str(cls.cert)],
                                capture_output=True, text=True, env=environment)
        if result.returncode:
            raise RuntimeError('Local fixture certificate generation failed: '+result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.mode = 'exact'
        self.server.requests = []
        self.server.stop = threading.Event()
        self.server.aborted = False
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05})
        self.thread.start()
        self.addCleanup(self.stop)
        self.job = types.SimpleNamespace(
            backend=cal.HTTPS_BACKEND, adapter=types.SimpleNamespace(name='lo'),
            address='127.0.0.1', port=self.server.server_address[1], deadline=time.monotonic()+10,
            attempt_seconds=1, attempt_payload=262144, lease_error=None,
            cancel=threading.Event(), inspect=mock.Mock())
        self.context = ssl.create_default_context(cafile=str(self.cert))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(transport, 'TLS_HOST', 'localhost').start()
        mock.patch.object(transport, 'tls_context', return_value=self.context).start()

    def stop(self):
        self.server.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def receiver(self):
        return transport.HTTPSReceiver(self.job, '127.0.0.1')

    def test_exact_and_chunked_payloads_are_tls_verified_and_application_paced(self):
        for mode in ('exact', 'chunked'):
            self.server.mode = mode
            receiver = self.receiver()
            duration = receiver.run()
            self.assertEqual(receiver.received, self.job.attempt_payload)
            self.assertGreaterEqual(duration, self.job.attempt_payload*8/cal.PUBLIC_RATE)
            self.assertTrue(receiver.evidence['tls_verified'])
            self.assertEqual(receiver.evidence['declared_bytes'],
                             None if mode == 'chunked' else self.job.attempt_payload)
            self.assertFalse(receiver.evidence['wire_rate_limited'])
            self.assertFalse(receiver.evidence['wire_bytes_capped'])
            self.assertGreater(receiver.evidence['tcp_unique_received_bytes'], 0)
        self.assertEqual(self.server.requests, ['/__down?bytes=262144']*2)

    def test_bad_payload_status_redirect_compression_and_framing_stop_once(self):
        for mode in ('truncated', 'wrong_length', 'status429', 'status503',
                     'redirect_same', 'redirect_private', 'gzip', 'duplicate', 'ambiguous',
                     'large_header', 'oversized_chunk', 'bad_chunk', 'extra_chunk'):
            self.server.mode = mode
            before = len(self.server.requests)
            receiver = self.receiver()
            with self.subTest(mode=mode), self.assertRaises(cal.CalibrationError):
                receiver.run()
            self.assertEqual(len(self.server.requests), before+1)
            self.assertLessEqual(receiver.received, self.job.attempt_payload)
            self.assertIsNone(receiver.evidence)

    def test_two_full_local_tls_receives_have_fresh_final_transport_not_wire_caps(self):
        self.job.attempt_payload = cal.PUBLIC_PAYLOAD
        self.job.attempt_seconds = 20
        records = []
        for _ in range(2):
            self.job.deadline = time.monotonic()+35
            receiver = self.receiver()
            before = int(Path('/sys/class/net/lo/statistics/rx_bytes').read_text())
            seconds = receiver.run()
            after = int(Path('/sys/class/net/lo/statistics/rx_bytes').read_text())
            self.assertEqual(receiver.received, cal.PUBLIC_PAYLOAD)
            self.assertTrue(18 <= receiver.evidence['tcp_progress_seconds'] <= 21)
            self.assertGreaterEqual(receiver.evidence['tcp_unique_received_bytes'], cal.PUBLIC_PAYLOAD*.9)
            self.assertGreaterEqual(after-before, cal.PUBLIC_PAYLOAD*.9)
            records.append({'payload_bytes': receiver.received, 'seconds': seconds,
                            'loopback_rx_delta': after-before, 'receiver_evidence': receiver.evidence})
        receipt = os.environ.get('CE_LOCAL_HTTPS_EVIDENCE')
        if receipt:
            Path(receipt).write_text(json.dumps({
                'fixture_only': True, 'actual_tls_host': 'localhost', 'actual_peer': '127.0.0.1',
                'actual_interface': 'lo', 'public_source_contacted': False, 'hardware_crc_measured': False,
                'driver_reopen_in_this_transport_test': False, 'rounds': records}, indent=2)+'\n')

    def test_stalled_connection_is_inconclusive(self):
        self.server.mode = 'stall'
        with self.assertRaisesRegex(cal.CalibrationError, 'stalled'):
            self.receiver().run()

    def test_tcp_refusal_is_explicit_and_has_no_payload_or_fallback(self):
        with socket.socket() as unused:
            unused.bind(('127.0.0.1', 0))
            self.job.port = unused.getsockname()[1]
            receiver = self.receiver()
            with self.assertRaisesRegex(cal.CalibrationError, 'HTTPS connection failed'):
                receiver.run()
            self.assertEqual(receiver.received, 0)
            self.assertIsNone(receiver.evidence)
        self.assertEqual(self.server.requests, [])

    def test_cancel_and_deadline_close_without_success_or_retry(self):
        self.server.mode = 'stall'
        timer = threading.Timer(.1, self.job.cancel.set)
        timer.start()
        self.addCleanup(timer.join)
        started = time.monotonic()
        with self.assertRaises(cal.Cancelled):
            self.receiver().run()
        self.assertLess(time.monotonic()-started, .5)
        self.job.cancel.clear()
        self.job.deadline = time.monotonic()+.1
        with self.assertRaisesRegex(cal.CalibrationError, 'timed out'):
            self.receiver().run()
        self.assertEqual(len(self.server.requests), 2)

    def test_untrusted_certificate_and_hostname_fail_without_payload(self):
        for context, hostname in ((ssl.create_default_context(), 'localhost'),
                                  (self.context, 'not-localhost.invalid')):
            receiver = self.receiver()
            with self.subTest(hostname=hostname), \
                    mock.patch.object(transport, 'tls_context', return_value=context), \
                    mock.patch.object(transport, 'TLS_HOST', hostname):
                with self.assertRaisesRegex(cal.CalibrationError, 'certificate verify'):
                    receiver.run()
            self.assertEqual(receiver.received, 0)
        self.assertEqual(self.server.requests, [])

    def test_insecure_context_and_oversized_attempt_are_refused(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with mock.patch.object(transport, 'tls_context', return_value=context):
            with self.assertRaisesRegex(cal.CalibrationError, 'cannot be bypassed'):
                self.receiver().run()
        self.job.attempt_payload = cal.PUBLIC_MAX_BYTES+1
        with self.assertRaisesRegex(cal.CalibrationError, 'limits'):
            self.receiver()
        self.assertEqual(self.server.requests, [])

    def test_lease_crc_and_source_provenance_are_not_hidden_by_https(self):
        self.job.lease_error = cal.CalibrationError('lease renewal failed')
        with self.assertRaisesRegex(cal.CalibrationError, 'rollback lease failed'):
            self.receiver().run()
        self.job.lease_error = None
        self.job.inspect.side_effect = cal.CRCError('actual CRC delta')
        with self.assertRaises(cal.CRCError):
            self.receiver().run()
        self.job.inspect.side_effect = None
        receiver = self.receiver()
        with mock.patch.object(socket, 'if_nametoindex', side_effect=[1, 2]):
            with self.assertRaisesRegex(cal.CalibrationError, 'interface/source/peer'):
                receiver.run()
        self.assertIsNone(receiver.evidence)

        receiver = self.receiver()
        receiver.stream = mock.Mock()
        receiver.stream.getsockopt.return_value = bytes(100)
        with self.assertRaisesRegex(cal.CalibrationError, 'cannot provide'):
            receiver.tcp_received()

    def test_transport_counter_reset_or_missing_kernel_support_cannot_certify(self):
        receiver = self.receiver()
        with mock.patch.object(receiver, 'tcp_received', side_effect=[100, 50]):
            with self.assertRaisesRegex(cal.CalibrationError, 'counters reset'):
                receiver.run()
        self.assertIsNone(receiver.evidence)


if __name__ == '__main__':
    unittest.main()
