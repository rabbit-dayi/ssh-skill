from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import support
from presentation import compact_result
from result_protocol import success_result, error_result
from ssh_skill import Dependencies, main, _legacy_subprocess_handler
from openssh_transport import TransportResult, supports_accept_new
from native_ssh_client import NativeSSHClient
from paramiko_client import ParamikoClient, SSHResult
import ssh_tunnel
import paramiko


def cli_result(result, flags=()):
    dependencies = Dependencies(**{name + '_handler': lambda args: result
        for name in ('exec', 'upload', 'download', 'transfer', 'cluster', 'config', 'tunnel', 'daemon', 'doctor')})
    out = io.StringIO()
    code = main([*flags, 'exec', 'example', 'opaque remote command'], dependencies, out)
    return code, json.loads(out.getvalue())


class PresentationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.raw = success_result('exec', {'alias': 'example', 'command': 'private code',
            'exit_code': 0, 'stdout': '{"nested": true}\n', 'stderr': '',
            'output': {'stdout': {'truncated': False, 'total_bytes': 17}}})

    def test_default_omits_redundancy_without_altering_remote_json(self):
        _, result = cli_result(self.raw)
        self.assertNotIn('command', result['data'])
        self.assertNotIn('stderr', result['data'])
        self.assertNotIn('output', result['data'])
        self.assertEqual({}, result['meta'])
        self.assertEqual(self.raw['data']['stdout'], result['data']['stdout'])
        self.assertEqual(0, result['data']['exit_code'])
        self.assertIn('command', self.raw['data'])  # no mutation of protocol object

    def test_verbose_preserves_full_protocol(self):
        _, result = cli_result(self.raw, ['--verbose'])
        self.assertEqual(self.raw, result)

    def test_small_limit_preserves_tail_and_reports_original_size(self):
        self.raw['data']['stdout'] = 'A' * 1000 + 'TAIL'
        _, result = cli_result(self.raw, ['--output-limit', '128'])
        self.assertTrue(result['data']['stdout'].endswith('TAIL'))
        self.assertEqual(1004, result['data']['output']['stdout']['total_bytes'])
        self.assertTrue(result['data']['output']['stdout']['truncated'])

    def test_unknown_result_keeps_request_id_warnings_and_outcome(self):
        raw = error_result('exec', code='outcome_unknown', message='timeout',
                           outcome='unknown', request_id='req-123', warnings=['important'])
        code, result = cli_result(raw)
        self.assertEqual(2, code)
        self.assertEqual('req-123', result['meta']['request_id'])
        self.assertEqual(['important'], result['meta']['warnings'])
        self.assertEqual('unknown', result['error']['outcome'])

    def test_diagnostic_limits_are_validated(self):
        for value in ('0', '-1', '999999'):
            code, result = cli_result(self.raw, ['--output-limit', value])
            self.assertEqual(1, code)
            self.assertEqual('invalid_arguments', result['error']['code'])

    def test_legacy_error_reason_is_not_discarded(self):
        completed = subprocess.CompletedProcess([], 1, '{"success":false,"error":"port occupied"}', '')
        with patch('ssh_skill._run_legacy_process', return_value=completed):
            result = _legacy_subprocess_handler('tunnel', 'ssh_tunnel.py', ['start'])
        self.assertEqual('port occupied', result['error']['message'])

    def test_help_and_doctor_do_not_require_site_packages(self):
        cli = support.SCRIPTS / 'ssh_skill.py'
        for args in (['--help'], ['doctor', '--json']):
            p = subprocess.run([sys.executable, '-S', str(cli), *args],
                               capture_output=True, text=True, timeout=15)
            self.assertEqual(0, p.returncode, p.stderr)


class TransportRegressionTests(unittest.TestCase):
    def test_client_version_probe_is_local_cached_and_cross_platform(self):
        supports_accept_new.cache_clear()
        try:
            with patch('openssh_transport.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'', b'OpenSSH_7.4p1')) as runner:
                self.assertFalse(supports_accept_new('old-ssh'))
                self.assertFalse(supports_accept_new('old-ssh'))
                runner.assert_called_once()
                self.assertEqual(['old-ssh', '-V'], runner.call_args.args[0])
            with patch('openssh_transport.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'', b'OpenSSH_for_Windows_9.5p1')):
                self.assertTrue(supports_accept_new('windows-ssh'))
        finally:
            supports_accept_new.cache_clear()

    def test_old_client_cannot_drop_custom_trust_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config'
            config.write_text('Host example\n UserKnownHostsFile /custom/trusted_hosts\n')
            client = NativeSSHClient('example.invalid', 'root', alias='example')
            with patch('native_ssh_client.find_openssh', return_value='ssh'), \
                 patch('native_ssh_client.supports_accept_new', return_value=False), \
                 patch('native_ssh_client.os.path.expanduser', return_value=str(config)), \
                 patch('paramiko_client.ParamikoClient.execute') as fallback:
                result = client.execute('action')
            self.assertEqual('configuration_unsupported', result.error_code)
            fallback.assert_not_called()

    def test_old_client_cannot_bypass_a_bastion(self):
        client = NativeSSHClient('example.invalid', 'root', proxy_jump='bastion')
        with patch('native_ssh_client.find_openssh', return_value='ssh'), \
             patch('native_ssh_client.supports_accept_new', return_value=False), \
             patch('native_ssh_client.run_openssh') as remote, \
             patch('paramiko_client.ParamikoClient.execute') as fallback:
            result = client.execute('action')
        self.assertEqual('openssh_option_unsupported', result.error_code)
        fallback.assert_not_called()
        remote.assert_not_called()

    def test_unsupported_accept_new_falls_back_only_before_dispatch(self):
        result = TransportResult(False, '', 'unsupported option "accept-new"', 255,
                                 error_code='openssh_option_unsupported')
        client = NativeSSHClient('example.invalid', 'root', key_file='example-key')
        with patch('native_ssh_client.find_openssh', return_value='ssh'), \
             patch('native_ssh_client.supports_accept_new', return_value=False), \
             patch('native_ssh_client.run_openssh') as remote, \
             patch('paramiko_client.ParamikoClient.execute', return_value=SSHResult(True, 'ok', '', 0)) as fallback:
            received = client.execute('one action')
        self.assertTrue(received.success)
        fallback.assert_called_once_with('one action')
        remote.assert_not_called()

    def test_no_fallback_for_unknown_outcome_or_proxy_requirements(self):
        for code, proxy in [('outcome_unknown', None), ('openssh_option_unsupported', 'bastion')]:
            result = TransportResult(False, '', 'error', 255, error_code=code)
            client = NativeSSHClient('example.invalid', 'root', proxy_jump=proxy)
            with patch('native_ssh_client.find_openssh', return_value='ssh'), \
                 patch('native_ssh_client.supports_accept_new', return_value=True), \
                 patch('native_ssh_client.run_openssh', return_value=result), \
                 patch('paramiko_client.ParamikoClient.execute') as fallback:
                client.execute('action')
            fallback.assert_not_called()

    def test_connection_timeout_is_known_not_dispatched(self):
        client = ParamikoClient('example.invalid', 'root')
        client._get_connection = Mock(side_effect=TimeoutError())
        result = client.execute('action')
        self.assertEqual('connection_timeout', result.error_code)
        self.assertEqual('failed', result.outcome)

    def test_authentication_failure_is_distinct(self):
        client = ParamikoClient('example.invalid', 'root')
        client._get_connection = Mock(side_effect=paramiko.AuthenticationException('secret should not be echoed'))
        result = client.execute('action')
        self.assertEqual('authentication_failed', result.error_code)
        self.assertNotIn('secret', result.stderr)

    def test_stdout_and_stderr_are_drained_concurrently(self):
        drained = threading.Event()
        class Channel:
            def recv_exit_status(self):
                return 0
        class Stream:
            channel = Channel()
            def __init__(self, wait):
                self.wait = wait
                self.sent = False
            def read(self, size):
                if self.sent:
                    return b''
                if self.wait:
                    if not drained.wait(1):
                        raise TimeoutError()
                else:
                    drained.set()
                self.sent = True
                return b'output'
        client = ParamikoClient('example.invalid', 'root')
        client._get_connection = lambda: SimpleNamespace(exec_command=lambda *a, **kw: (None, Stream(True), Stream(False)))
        result = client.execute('no deadlock')
        self.assertTrue(result.success)
        self.assertEqual('output', result.stdout)
        self.assertEqual('output', result.stderr)

    def test_disconnect_after_dispatch_is_unknown(self):
        client = ParamikoClient('example.invalid', 'root')
        client._get_connection = lambda: SimpleNamespace(exec_command=Mock(side_effect=EOFError()))
        result = client.execute('action')
        self.assertEqual('outcome_unknown', result.error_code)
        self.assertEqual('unknown', result.outcome)

    def test_exec_always_supplies_request_id(self):
        import ssh_execute
        with patch('config_v3.SSHConfigLoaderV3') as loader, \
             patch('ssh_execute.direct_execute', return_value={'success': True, 'stdout': 'ok', 'exit_code': 0}):
            loader.return_value.get_connection_params.return_value = {}
            a = ssh_execute.run_exec('host', 'action')
            b = ssh_execute.run_exec('host', 'action')
        self.assertTrue(a['meta']['request_id'])
        self.assertNotEqual(a['meta']['request_id'], b['meta']['request_id'])


class TunnelRegressionTests(unittest.TestCase):
    def test_authentication_failure_is_not_retried_on_reconnect(self):
        tunnel = ssh_tunnel.SSHTunnel('example', 12345, 'service', 80)
        tunnel._running = True
        tunnel._connect_ssh = Mock(side_effect=paramiko.AuthenticationException())
        tunnel._reconnect_ssh()
        tunnel._connect_ssh.assert_called_once()
        self.assertFalse(tunnel._running)

    def test_forwarding_propagates_half_close(self):
        local, accepted = socket.socketpair()
        channel, remote = socket.socketpair()
        local.settimeout(2)
        remote.settimeout(2)
        tunnel = ssh_tunnel.SSHTunnel('example', 12345, 'service', 80)
        worker = threading.Thread(target=tunnel._forward_data, args=(accepted, channel), daemon=True)
        worker.start()
        try:
            local.sendall(b'request')
            self.assertEqual(b'request', remote.recv(100))
            local.shutdown(socket.SHUT_WR)
            self.assertEqual(b'', remote.recv(100))
            remote.sendall(b'response')
            remote.shutdown(socket.SHUT_WR)
            self.assertEqual(b'response', local.recv(100))
            self.assertEqual(b'', local.recv(100))
            worker.join(2)
            self.assertFalse(worker.is_alive())
        finally:
            for sock in (local, accepted, channel, remote):
                sock.close()

    def test_identical_tunnel_is_reused_but_different_target_is_rejected(self):
        args = SimpleNamespace(alias='example', local_port=12345, remote_host='service', remote_port=80)
        for host, expected in [('service', 0), ('other-service', 1)]:
            with patch('ssh_tunnel.read_tunnel_info', return_value={'remote_host': host, 'remote_port': 80}), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                result = ssh_tunnel.cmd_start(args)
            self.assertEqual(expected, result)
            self.assertEqual(expected == 0, json.loads(out.getvalue())['success'])

    def test_tunnel_preserves_proxy_jump(self):
        tunnel = ssh_tunnel.SSHTunnel('example', 12345, 'service', 80)
        tunnel._connection_params = {'hostname': 'remote', 'user': 'root', 'port': 22,
                                     'key_file': 'key', 'proxy_jump': 'bastion'}
        proxy = Mock()
        tunnel._create_proxy_client = Mock(return_value=proxy)
        with patch('paramiko.SSHClient') as client, patch('ssh_tunnel.configure_paramiko_host_keys'):
            tunnel._connect_ssh()
        tunnel._create_proxy_client.assert_called_once_with('bastion')
        self.assertIs(tunnel._proxy_client, proxy)
        self.assertIsNotNone(client.return_value.connect.call_args.kwargs['sock'])

    @unittest.skipIf(os.name == 'nt', 'Unix inherited-pipe regression')
    def test_background_tunnel_does_not_keep_callers_pipe_open(self):
        with tempfile.TemporaryDirectory() as directory:
            code = '''import json,os,sys,time
from types import SimpleNamespace
sys.path[:0]=[sys.argv[1],sys.argv[2]]
import ssh_tunnel as m
m.TUNNEL_DIR=sys.argv[3]
def fake_start(self):
 p=m.get_tunnel_info_path(self.tunnel_id)
 with open(p,'w') as f:json.dump({'pid':os.getpid(),'tunnel_id':self.tunnel_id},f)
 time.sleep(20)
m.SSHTunnel.start=fake_start
raise SystemExit(m.cmd_start(SimpleNamespace(alias='fake',local_port=int(sys.argv[4]),remote_host='service',remote_port=80)))
'''
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            p = subprocess.Popen([sys.executable, '-c', code, str(support.SCRIPTS), str(support.LIB), directory, str(port)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                out, err = p.communicate(timeout=7)
                self.assertEqual(0, p.returncode, err)
                self.assertTrue(json.loads(out)['success'])
            finally:
                for info in Path(directory).glob('*.json'):
                    try:
                        os.kill(json.loads(info.read_text())['pid'], signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if p.poll() is None:
                    p.kill()
                    p.communicate()
