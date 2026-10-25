import hashlib
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import TestCase
from .models import AgentRun


class AgentApiTests(TestCase):
    def setUp(self):
        self.old_secret = os.environ.get('VULFOCUS_AGENT_CONTROL_TOKEN')
        os.environ['VULFOCUS_AGENT_CONTROL_TOKEN'] = 'controller-secret'
        self.token = 't' * 48
        self.control = {'HTTP_X_AGENT_CONTROL_TOKEN': 'controller-secret'}
        self.agent = {'HTTP_X_AGENT_RUN': 'case-1',
                      'HTTP_AUTHORIZATION': 'Bearer ' + self.token}

    def tearDown(self):
        if self.old_secret is None:
            os.environ.pop('VULFOCUS_AGENT_CONTROL_TOKEN', None)
        else:
            os.environ['VULFOCUS_AGENT_CONTROL_TOKEN'] = self.old_secret

    def post(self, path, value=None, **headers):
        return self.client.post('/agent/v1/' + path + '/',
            data=json.dumps(value or {}), content_type='application/json', **headers)

    def provision(self):
        with patch('agentapi.views.runtime.unit', return_value={'status': 'ready'}):
            return self.post('runs', {'run_key': 'case-1',
                'challenge_key': 'catalog/example', 'agent_token': self.token}, **self.control)

    def test_bound_identity_and_no_answer_fields(self):
        self.assertEqual(self.post('runs', {}, **{}).status_code, 401)
        self.assertEqual(self.provision().status_code, 200)
        self.assertEqual(self.provision().status_code, 200)
        self.assertEqual(AgentRun.objects.count(), 1)
        with patch('agentapi.views.runtime.public_challenge', return_value={
                'title': 'Example', 'description': 'question'}):
            response = self.client.get('/agent/v1/challenge/', **self.agent)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('flag', response.content.decode())
        self.assertNotIn('token', response.content.decode())

    def test_submit_and_solved_restart(self):
        self.provision()
        run = AgentRun.objects.get(run_key='case-1')
        run.status = 'running'
        run.generation = 1
        run.flag_sha256 = hashlib.sha256(b'flag-{test}').hexdigest()
        run.save()
        self.assertFalse(self.post('submit', {'flag': 'wrong'}, **self.agent).json()['data']['correct'])
        self.assertTrue(self.post('submit', {'flag': 'flag-{test}'}, **self.agent).json()['data']['correct'])
        self.assertEqual(self.post('restart', {}, **self.agent).status_code, 409)
        result = self.client.get('/agent/v1/result/?run_key=case-1', **self.control).json()['data']
        self.assertTrue(result['solved'])

    @patch('agentapi.views.launch.delay')
    def test_start_is_idempotent(self, delay):
        self.provision()
        self.assertEqual(self.post('start', {}, **self.agent).status_code, 202)
        self.assertEqual(self.post('start', {}, **self.agent).status_code, 202)
        self.assertEqual(delay.call_count, 1)

    @patch('agentapi.tasks.runtime.stop_run')
    @patch('agentapi.tasks.runtime.start_run')
    def test_cancellation_during_start_cannot_resurrect_run(self, start_run, stop_run):
        from .tasks import launch
        self.provision()
        run = AgentRun.objects.get(run_key='case-1')
        run.status = 'starting'
        run.save()

        def cancelled(current, flag):
            AgentRun.objects.filter(pk=current.pk).update(status='stopping')
            return current.project, {'80/tcp': '127.0.0.1:30000'}

        start_run.side_effect = cancelled
        launch.run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, 'stopping')
        stop_run.assert_called_once()

    def test_override_replaces_fixed_host_ports(self):
        from .runtime import write_override
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.yml'
            output = Path(directory) / 'override.yml'
            source.write_text('services:\n  master:\n    ports:\n      - "8080:8080"\n'
                              '  slave:\n    ports:\n      - "8081:8081"\n')
            write_override(source, 'master', {'vul_flag': 'test-flag'}, output)
            text = output.read_text()
        self.assertEqual(text.count('!override'), 2)
        self.assertNotIn('8080:8080', text)
        self.assertNotIn('8081:8081', text)
        self.assertIn('vul_flag', text)

    def test_challenge_title_and_flag_status_resolve_by_image_name(self):
        from .runtime import public_challenge
        key = 'catalog/example'
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'compose.yml').write_text(
                'services:\n  target:\n    ports:\n      - "80/tcp"\n')
            Path(directory, 'catalog-api.json').write_text(json.dumps({'imgs': [{
                'image_id': 'different-id', 'image_name': 'vulfocus/example:latest',
                'image_vul_name': 'Example Vulnerability',
                'image_desc': 'Question text', 'is_flag': 1}]}))
            with patch('agentapi.runtime.package_root', return_value=Path(directory)), \
                 patch('agentapi.runtime.unit', return_value={
                     'title': key, 'tasks': ['image-key'],
                     'compose': 'compose.yml', 'services': {'target': 'image-key'}}), \
                 patch('agentapi.runtime.catalog', return_value={
                     'images': {'image-key': {
                         'image': 'vulfocus/example:latest', 'archive_tags': []}}}):
                result = public_challenge(key)
        self.assertEqual(result['title'], 'Example Vulnerability')
        self.assertEqual(result['description'], 'Question text')
        self.assertEqual(result['flag_support_status'], 'supported')

    @patch('agentapi.runtime.locked', side_effect=AssertionError('unnecessary import lock'))
    @patch('agentapi.runtime.client')
    @patch('agentapi.runtime.catalog')
    def test_existing_verified_image_skips_import_lock(self, catalog, docker_client, _lock):
        from .runtime import ensure_images
        catalog.return_value = {'images': {'image-key': {
            'status': 'verified', 'architecture': 'amd64',
            'image_id': 'sha256:expected'}}}
        docker_client.images.get.return_value.id = 'sha256:expected'
        ensure_images({'tasks': ['image-key']})
        docker_client.images.get.assert_called_once_with('ctf-offline/runtime:image-key')
