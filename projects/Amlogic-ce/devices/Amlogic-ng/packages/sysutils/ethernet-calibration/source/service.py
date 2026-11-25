# SPDX-License-Identifier: GPL-2.0-or-later
"""Local root-only calibration worker and pre-ConnMan profile application."""

import argparse
import json
import logging
import os
from pathlib import Path
import signal
import socket
import struct
import threading
import time

from calibration import (CalibrationError, Cancelled, CRCError, DiagnosticJob, Health, HTTPS_BACKEND, Inconclusive, Job, ProfileStore, PublicRepairJob,
                         adapters, endpoint, validate_profile)
from public_servers import catalogue_metadata, catalogue_page

SOCKET_PATH = Path('/run/ethernet-calibration/control')
BOOT_STATUS = Path('/run/ethernet-calibration-boot.json')
MAX_MESSAGE = 8192
log = logging.getLogger('ethernet-calibration')


def boot_status(value):
    temporary = BOOT_STATUS.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream)
    os.chmod(temporary, 0o600)
    os.replace(temporary, BOOT_STATUS)


def apply_profile():
    """Validate stored evidence and apply timing; never run boot traffic tests."""
    store = ProfileStore()
    try:
        profile = store.load()
        if profile is None:
            boot_status({'state': 'not_saved'})
            return 0
        deadline = time.monotonic() + 20
        found = []
        while time.monotonic() < deadline:
            found = adapters()
            if found:
                break
            time.sleep(0.2)
        matching = [item for item in found if item.identity == profile.get('identity')]
        if len(matching) != 1:
            raise CalibrationError('Saved profile has no matching supported adapter/backend')
        adapter = matching[0]
        index = validate_profile(profile, adapter.identity)
        adapter.write('profile ' + str(index))
        capabilities = adapter.capabilities()
        if capabilities.get('error'):
            raise CalibrationError('Driver timing application failed (%s)' % capabilities['error'])
        if capabilities['index'] != index:
            raise CalibrationError('Driver did not accept saved timing')
        boot_status({'state': 'applied' if capabilities['applied'] else 'pending',
                     'index': index})
        return 0
    except (CalibrationError, OSError, ValueError, KeyError) as error:
        log.error('Saved Ethernet calibration application failed: %s', error)
        boot_status({'state': 'failed', 'error': str(error)})
        return 1


class Service:
    def __init__(self, store=None):
        self.store = store or ProfileStore()
        self.job = None
        self.thread = None
        self.result = {'phase': 'idle'}
        self.lock = threading.Lock()
        self.health = Health()
        self.current_adapter = None
        self.operation_started = None

    def update(self, result):
        with self.lock:
            self.result = result
        if result.get('candidate_error'):
            log.info('Timing candidate %s rejected during %s: %s',
                     result.get('candidate'), result['phase'], result['candidate_error'])
        if result.get('candidate_inconclusive'):
            log.info('Timing candidate %s inconclusive; recovering original Ethernet: %s',
                     result.get('candidate'), result['candidate_inconclusive'])

    def run_job(self, job):
        try:
            job.run()
        except Cancelled as error:
            log.info('%s', error)
            self.update({'phase': 'cancelled', 'error': str(error)})
        except CRCError as error:
            log.error('Ethernet packet checks failed: %s', error)
            self.update({'phase': 'corruption', 'error': str(error)})
        except Inconclusive as error:
            log.warning('Ethernet test was inconclusive: %s', error)
            self.update({'phase': 'inconclusive', 'error': str(error)})
        except (CalibrationError, OSError, ValueError, KeyError) as error:
            log.error('Ethernet calibration failed: %s', error)
            self.update({'phase': 'failed', 'error': str(error)})
        finally:
            if self.result.get('phase') == 'complete' and job.last_measurement is not None:
                self.health.verified(job.adapter, job.last_measurement)
            elif self.result.get('phase') == 'public_complete':
                self.health.reset()
                self.health.identity = job.adapter.identity
                self.health.baseline = job.last_measurement['ended'], job.last_measurement['after']
                self.health.state = {'state': 'insufficient_traffic', 'crc_delta': 0,
                                     'window_seconds': job.last_measurement['evidence']['seconds']}
            elif self.result.get('phase') == 'diagnostic_complete':
                observation = job.last_measurement
                self.health.reset()
                self.health.identity = job.adapter.identity
                self.health.baseline = observation['started'], observation['before']
                self.health.state = {
                    'state': 'corruption' if self.result['diagnostic']['crc_delta'] else 'insufficient_traffic',
                    'crc_delta': self.result['diagnostic']['crc_delta'],
                    'window_seconds': observation['evidence']['seconds'],
                    'rx_bytes': self.result['diagnostic']['bytes'],
                }
            else:
                self.health.reset()
                if isinstance(job, DiagnosticJob) and hasattr(job, 'before'):
                    self.health.identity = job.adapter.identity
                    self.health.baseline = job.started, job.before
                    self.sample_health(job.adapter)

    def run_control(self, action, adapter, index=None):
        try:
            self.control(action, adapter, index)
        except (CalibrationError, OSError, ValueError, KeyError) as error:
            log.error('Ethernet settings update failed: %s', error)
            self.update({'phase': 'failed', 'error': str(error)})

    def sample_health(self, adapter):
        try:
            self.health.sample(adapter, time.monotonic())
        except (CalibrationError, OSError, ValueError) as error:
            log.error('Ethernet health sampling failed: %s', error)
            self.health.reset()
            self.health.state = {'state': 'unknown', 'error': str(error)}

    def status(self):
        busy = self.thread is not None and self.thread.is_alive()
        with self.lock:
            result = dict(self.result)
        if busy and self.operation_started is not None:
            result['elapsed_seconds'] = time.monotonic() - self.operation_started
        if busy and result.get('recovery_deadline') is not None:
            result['recovery_remaining_seconds'] = max(0, result['recovery_deadline'] - time.monotonic())
        result['public_catalogue'] = catalogue_metadata()
        if busy:
            # PHY reopen holds the driver lock; progress/cancel must not wait on it.
            result.update({'version': 1, 'busy': True, 'supported': True,
                           'adapter': {'name': (self.current_adapter or self.job.adapter).name,
                                       'identity': (self.current_adapter or self.job.adapter).identity},
                           'profile': {'state': 'inactive' if (
                               self.store.directory / 'active.json').exists() else 'not_saved'},
                           'health': {'state': 'reconnecting' if self.job is None else 'calibrating'}})
            return result
        found = adapters()
        result.update({'version': 1, 'busy': busy, 'supported': len(found) == 1})
        if len(found) != 1:
            result['health'] = {'state': 'unsupported'}
            result['profile'] = {'state': 'inactive' if (
                self.store.directory / 'active.json').exists() else 'not_saved'}
            return result
        adapter = found[0]
        self.sample_health(adapter)
        profile_error = None
        try:
            profile = self.store.load()
        except CalibrationError as error:
            log.error('Saved profile is unreadable; clear it before recalibration: %s', error)
            profile = None
            profile_error = str(error)
        capabilities = adapter.capabilities()
        if capabilities.get('error'):
            result['application_error'] = 'Driver timing application failed (%s)' % capabilities['error']
        profile_state = 'inactive' if profile_error else 'not_saved'
        profile_margin = None
        valid_profile = None
        if profile_error:
            result['application_error'] = profile_error
        if profile is not None:
            try:
                index = validate_profile(profile, adapter.identity)
                valid_profile = profile
                if 'window' in profile:
                    profile_margin = ('reduced' if profile['window'][1] - profile['window'][0] < 2
                                      else 'normal')
                profile_state = ('applied' if capabilities['applied'] and not capabilities.get('error')
                                 else 'pending') if (
                    capabilities['index'] == index and not capabilities['lease']) else 'inactive'
                if profile.get('kind') == 'public_error_checked':
                    result['last_verification'] = {
                        'index': index, 'verified_at': profile.get('verified_at'),
                        'kind': 'public_error_checked', 'gigabit_verified': False, 'state': 'saved',
                        'traffic_backend': profile.get('traffic_backend', 'iperf3')}
                elif profile.get('kind') == 'manual_unverified':
                    result.pop('last_verification', None)
                    result['manual_timing'] = {
                        'index': index, 'manually_set_at': profile['manually_set_at'],
                        'gigabit_verified': False}
                else:
                    result['last_verification'] = {
                        'index': index, 'verified_at': profile.get('verified_at'),
                        'send_bps': profile['verification']['send']['bps'],
                        'receive_bps': profile['verification']['receive']['bps'], 'state': 'saved'}
            except CalibrationError as error:
                log.error('Saved calibration cannot be applied: %s', error)
                profile_state = 'inactive'
        if BOOT_STATUS.exists():
            boot = json.loads(BOOT_STATUS.read_text())
            if boot['state'] == 'failed' and profile_state != 'applied':
                result['application_error'] = boot.get('error')
        result['adapter'] = {'name': adapter.name, 'identity': adapter.identity,
                             'gigabit_full': adapter.link_ready()}
        result['profile'] = {'state': profile_state, 'index': capabilities['index']}
        result['timing'] = {
            'index': capabilities['default_index'] if capabilities['index'] < 0 else capabilities['index'],
            'factory': capabilities['index'] < 0,
            'state': 'failed' if capabilities.get('error') else
                     'temporary' if capabilities['lease'] else
                     'applied' if capabilities['applied'] else 'pending'}
        if valid_profile is not None:
            result['profile']['kind'] = valid_profile.get('kind', 'lan_gigabit')
            result['profile']['configured_index'] = valid_profile['index']
            result['profile']['traffic_backend'] = valid_profile.get('traffic_backend', 'iperf3')
        if profile_margin is not None:
            result['profile']['margin'] = profile_margin
        result['health'] = {'state': 'calibrating'} if busy else dict(self.health.state)
        return result

    def request(self, request):
        if not isinstance(request, dict) or request.get('version') != 1:
            raise CalibrationError('Unsupported control protocol')
        action = request.get('action')
        if action == 'status':
            return self.status()
        if action == 'catalogue':
            try:
                return dict(catalogue_page(request.get('region'), request.get('offset', 0)), version=1)
            except ValueError as error:
                raise CalibrationError(str(error)) from error
        if action == 'cancel':
            if self.job is not None:
                self.job.cancel.set()
            return {'version': 1, 'cancel_requested': self.job is not None}
        if self.thread is not None and self.thread.is_alive():
            raise CalibrationError('An Ethernet calibration is already running')
        found = adapters()
        if len(found) != 1:
            raise CalibrationError('This adapter has no supported calibration backend')
        adapter = found[0]
        if action == 'start':
            public = request.get('public', False)
            consent = request.get('consent', False)
            if type(public) is not bool or type(consent) is not bool:
                raise CalibrationError('Invalid public endpoint consent')
            preset = request.get('preset')
            backend = request.get('backend', 'iperf3')
            if backend == HTTPS_BACKEND:
                if not public or not consent or any(request.get(key) is not None for key in
                                                    ('address', 'port', 'preset', 'url')):
                    raise CalibrationError('Experimental HTTPS needs explicit public-source consent and no custom URL')
                address, port = None, None
            elif backend != 'iperf3':
                raise CalibrationError('Unsupported traffic backend')
            elif preset is not None:
                if not public or not consent:
                    raise CalibrationError('Public presets require explicit diagnostic consent')
                address, port = None, request.get('port')
                if request.get('address') is not None:
                    raise CalibrationError('Choose either a public preset or a custom address')
            else:
                address, port = endpoint(request.get('address'), request.get('port'), public, consent)
            job_type = PublicRepairJob if public else Job
            options = {'preset': preset}
            if backend != 'iperf3':
                options['backend'] = backend
            self.job = job_type(adapter, self.store, address, port, public, self.update, **options)
            self.health.reset()
            self.current_adapter = adapter
            self.operation_started = time.monotonic()
            self.update({'phase': 'starting'})
            self.thread = threading.Thread(target=self.run_job, args=(self.job,), daemon=True)
            self.thread.start()
            return {'version': 1, 'started': True}
        if action in ('clear', 'restore', 'manual'):
            index = request.get('index') if action == 'manual' else None
            if action == 'manual' and (
                    type(index) is not int or not 0 <= index <= 31 or request.get('consent') is not True):
                raise CalibrationError('Manual timing requires an index from 0 to 31 and explicit consent')
            self.health.reset()
            self.job = None
            self.current_adapter = adapter
            self.operation_started = time.monotonic()
            self.update({'phase': {'clear': 'clearing', 'restore': 'restoring',
                                   'manual': 'manual_saving'}[action]})
            self.thread = threading.Thread(target=self.run_control, args=(action, adapter, index), daemon=True)
            self.thread.start()
            return {'version': 1, 'started': True}
        raise CalibrationError('Unknown control action')

    def control(self, action, adapter, index=None):
        if action == 'manual':
            if type(index) is not int or not 0 <= index <= 31:
                raise CalibrationError('Manual timing index must be from 0 to 31')
            before = adapter.capabilities()
            if before['lease'] or before.get('error'):
                raise CalibrationError('Existing Ethernet timing transaction/error must be resolved first')
            profile = {'schema': 3, 'kind': 'manual_unverified', 'identity': adapter.identity,
                       'index': index, 'manually_set_at': time.time()}
            applied = False
            def apply():
                nonlocal applied
                applied = True
                adapter.write('profile ' + str(index))
                cap = adapter.capabilities()
                if cap['lease'] or cap.get('error') or cap['index'] != index:
                    raise CalibrationError('Driver did not accept manually entered timing')
                boot_status({'state': 'applied' if cap['applied'] else 'pending',
                             'index': index, 'kind': 'manual_unverified'})
            try:
                self.store.save(profile, adapter.identity, apply)
            except (OSError, CalibrationError) as error:
                if applied:
                    try:
                        adapter.write('default' if before['index'] < 0 else 'profile ' + str(before['index']))
                        restored = adapter.capabilities()
                        if (restored['index'] != before['index'] or restored['lease'] or
                                restored.get('error')):
                            raise CalibrationError('Original driver timing was not restored')
                    except (OSError, CalibrationError) as restore_error:
                        raise CalibrationError('Manual timing save failed (%s); '
                                               'original runtime restoration also failed (%s)' %
                                               (error, restore_error)) from restore_error
                raise
            self.health.reset()
            self.update({'phase': 'manually_set', 'index': index})
            return
        if action == 'clear':
            snapshot = self.store.snapshot()
            try:
                previous = self.store.load()
                if previous is not None:
                    validate_profile(previous, adapter.identity)
            except CalibrationError as error:
                log.warning('Clearing invalid calibration profile at user request: %s', error)
                previous = None
            preserve_lan = False
            if previous is not None and previous.get('kind') in ('public_error_checked', 'manual_unverified'):
                try:
                    older = self.store.load('previous')
                    if older is not None and older.get('schema') == 1:
                        validate_profile(older, adapter.identity)
                        preserve_lan = True
                except CalibrationError as error:
                    log.warning('Previous profile is not verified LAN history: %s', error)
            # Do not claim cleared persistence unless both steps succeeded.
            old_index = adapter.capabilities()['index']
            adapter.write('default')
            try:
                self.store.remove_active()
                if previous is not None and not preserve_lan:
                    self.store.replace('previous', previous)
            except (OSError, CalibrationError):
                try:
                    self.store.restore(snapshot)
                finally:
                    if old_index >= 0:
                        adapter.write('profile ' + str(old_index))
                raise
            boot_status({'state': 'not_saved'})
            self.health.reset()
            self.update({'phase': 'cleared'})
            return
        if action == 'restore':
            profile = self.store.load('previous')
            index = validate_profile(profile, adapter.identity)
            self.store.save(profile, adapter.identity,
                            lambda: adapter.write('profile ' + str(index)))
            adapter.wait_ready(threading.Event())
            capabilities = adapter.capabilities()
            if (capabilities.get('error') or capabilities['index'] != index or
                    capabilities.get('lease') or not capabilities['applied']):
                raise CalibrationError('Saved Ethernet setting was not applied')
            boot_status({'state': 'applied', 'index': index})
            self.health.reset()
            self.update({'phase': 'restored'})
            return
        raise CalibrationError('Unknown control action')

    def shutdown(self, *_):
        if self.job is not None:
            self.job.cancel.set()
        self.stopping = True

    def serve(self):
        self.stopping = False
        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)
        SOCKET_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(SOCKET_PATH.parent, 0o700)
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
            server.bind(str(SOCKET_PATH))
            os.chmod(SOCKET_PATH, 0o600)
            server.listen(4)
            server.settimeout(1)
            sampled = 0
            while not self.stopping:
                if time.monotonic() - sampled >= 5 and not (
                        self.thread is not None and self.thread.is_alive()):
                    try:
                        found = adapters()
                        if len(found) == 1:
                            self.sample_health(found[0])
                    except (CalibrationError, OSError, ValueError) as error:
                        log.error('Ethernet health sampling failed: %s', error)
                        self.health.reset()
                        self.health.state = {'state': 'unknown', 'error': str(error)}
                    sampled = time.monotonic()
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    connection.settimeout(2)
                    try:
                        _, uid, _ = struct.unpack('3i', connection.getsockopt(
                            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                        if uid != 0:
                            raise CalibrationError('Calibration control requires local root identity')
                        message = connection.recv(MAX_MESSAGE + 1)
                        if len(message) > MAX_MESSAGE:
                            raise CalibrationError('Control message is too large')
                        response = self.request(json.loads(message))
                        response['ok'] = True
                    except (CalibrationError, OSError, ValueError, KeyError, TypeError) as error:
                        log.error('Calibration control request failed: %s', error)
                        response = {'version': 1, 'ok': False, 'error': str(error)}
                    try:
                        connection.sendall(json.dumps(response, allow_nan=False).encode())
                    except (OSError, ValueError) as error:
                        log.error('Calibration control response failed: %s', error)
        if self.thread is not None:
            self.thread.join(timeout=3)
        SOCKET_PATH.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if options.apply:
        raise SystemExit(apply_profile())
    Service().serve()
