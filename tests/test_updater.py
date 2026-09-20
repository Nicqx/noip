import base64
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import updater
import manage


CONFIG = {'hostname': 'pmqxyz.hopto.org', 'interval_seconds': 300}
AUTH = ('test-user', 'test-password')


class NoIPTests(unittest.TestCase):
    def http(self, body, status=200):
        return Mock(side_effect=[(200, '8.8.8.8'), (status, body)])

    def test_credentials_are_parsed_without_shell_evaluation(self):
        self.assertEqual(updater.credentials('DDNS_USERNAME="test"\nDDNS_PASSWORD="$(echo unsafe);`id`"'),
                         ('test', '$(echo unsafe);`id`'))
        with self.assertRaises(updater.ClientError): updater.assignments('echo unsafe')

    def test_success_then_unchanged_ip_sends_no_update(self):
        http = self.http('good 8.8.8.8')
        state, _ = updater.step(CONFIG, AUTH, {}, 100, http)
        self.assertEqual(state['last_ip'], '8.8.8.8')
        http = Mock(return_value=(200, '8.8.8.8'))
        updater.step(CONFIG, AUTH, state, 400, http)
        self.assertEqual(http.call_count, 1)
        self.assertEqual(http.call_args.args, (updater.IP_URL,))

    def test_success_prefix_is_not_enough(self):
        state, message = updater.step(CONFIG, AUTH, {}, 100, self.http('goodness 8.8.8.8'))
        self.assertNotIn('last_ip', state)
        self.assertEqual(state['retry_at'], 1900)

    def test_wrong_returned_ip_is_not_accepted(self):
        state, _ = updater.step(CONFIG, AUTH, {}, 100, self.http('good 1.1.1.1'))
        self.assertNotIn('last_ip', state)

    def test_provider_failures_back_off_across_restart(self):
        for status, body in [(200, '911'), (500, ''), (429, '')]:
            state, _ = updater.step(CONFIG, AUTH, {}, 100, self.http(body, status))
            self.assertEqual(state['retry_at'], 1900)
            http = Mock()
            updater.step(CONFIG, AUTH, json.loads(json.dumps(state)), 1899, http)
            http.assert_not_called()

    def test_permanent_error_stops_until_configuration_changes(self):
        state, message = updater.step(CONFIG, AUTH, {}, 100, self.http('badauth'))
        self.assertTrue(state['blocked']); self.assertNotIn(AUTH[1], message)
        http = Mock()
        updater.step(CONFIG, AUTH, state, 100000, http); http.assert_not_called()
        state, _ = updater.step(CONFIG, ('new-user', 'new-password'), state, 100000, self.http('nochg 8.8.8.8'))
        self.assertNotIn('blocked', state)

    def test_invalid_or_private_ipv4_never_sends_credentials(self):
        for value in ['192.168.1.10', '<html>error</html>', '::1']:
            http = Mock(return_value=(200, value))
            state, _ = updater.step(CONFIG, AUTH, {}, 0, http)
            self.assertEqual(http.call_count, 1)
            self.assertNotIn('last_ip', state)

    def test_redirects_are_refused(self):
        self.assertIsNone(updater.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid'))

    def test_new_install_is_disabled_and_existing_nuc_stays_enabled(self):
        kube = Mock()
        secret = {'data': {'credentials.json': base64.b64encode(json.dumps({'username': 'test', 'password': 'test'}).encode()).decode()}}
        kube.get.side_effect = lambda kind, name: ({'spec': {'replicas': 1}} if kind == 'deployment' else secret if kind == 'secret' else None)
        self.assertEqual(manage.render()[1]['spec']['replicas'], 0)
        self.assertEqual(manage.render(kube)[1]['spec']['replicas'], 1)
        template = manage.render(kube)[1]['spec']['template']['spec']
        self.assertTrue(template['securityContext']['runAsNonRoot'])
        self.assertTrue(template['containers'][0]['securityContext']['readOnlyRootFilesystem'])

    def test_legacy_hostname_is_preserved(self):
        kube = Mock(); kube.get.return_value = {'data': {'ddns.conf': 'DDNS_HOSTNAME="custom.hopto.org"\nCHECK_INTERVAL_SECONDS="600"'}}
        self.assertEqual(manage.configuration(kube), {'hostname': 'custom.hopto.org', 'interval_seconds': 600})
