import pytest
from unittest.mock import MagicMock

from crczp.sandbox_ansible_app.lib.ansible import AllocationAnsibleRunner
from crczp.sandbox_ansible_app.lib.container import DockerContainer
from crczp.sandbox_instance_app.models import Sandbox
from crczp.sandbox_instance_app.lib import sandboxes

pytestmark = pytest.mark.django_db


class TestPrepareInventoryFile:
    @pytest.fixture(autouse=True)
    def set_up(self, mocker, top_ins):
        self.client = mocker.patch('crczp.sandbox_common_lib.utils.get_terraform_client')
        self.client.get_sandbox.return_value = top_ins
        self.save_file = mocker.patch(
            'crczp.sandbox_ansible_app.lib.ansible.AnsibleRunner.save_file')
        yield

    def test_prepare_inventory_file_success(self, mocker, top_ins):
        mock_inventory = mocker.patch('crczp.sandbox_ansible_app.lib.ansible.Inventory')
        mocker.patch('crczp.sandbox_ansible_app.lib.ansible.docker.from_env')
        sandboxes.get_topology_instance = mocker.MagicMock()
        sandboxes.get_topology_instance.return_value = top_ins

        dir_path = '/tmp'
        sandbox = Sandbox.objects.get(pk=1)
        AllocationAnsibleRunner(dir_path).prepare_inventory_file(sandbox)

        mock_inventory.assert_called_once()

    def test_prepare_inventory_object(self, mocker, top_ins, inventory):
        mocker.patch('crczp.sandbox_ansible_app.lib.ansible.docker.from_env')
        dir_path = mocker.MagicMock()
        sandbox = Sandbox.objects.get(pk=1)
        sandbox.allocation_unit.pool.get_pool_prefix = mocker.MagicMock()
        sandbox.allocation_unit.pool.get_pool_prefix.return_value = 'pool-prefix'
        sandbox.allocation_unit.get_stack_name = mocker.MagicMock()
        sandbox.allocation_unit.get_stack_name.return_value = 'stack-name'
        result = AllocationAnsibleRunner(dir_path).create_inventory(sandbox)

        assert result.to_dict() == inventory


class TestDockerContainerCommand:
    def test_run_container_includes_answers_storage_api(self, settings):
        settings.CRCZP_CONFIG.answers_storage_api = 'http://answers-storage:8087/answers-storage/api/v1'
        stage = MagicMock()

        DockerContainer('url', 'rev', stage, 'ssh_dir', 'inventory_path',
                        'containers_path', 'credentials_path')

        command = DockerContainer.CLIENT().containers.run.call_args.kwargs['command']
        assert command == [
            '-u', 'url',
            '-r', 'rev',
            '-i', '/app/inventory.yml',
            '-a', 'http://answers-storage:8087/answers-storage/api/v1',
        ]

    def test_run_container_skips_answers_storage_api_when_not_configured(self, settings):
        settings.CRCZP_CONFIG.answers_storage_api = ''
        stage = MagicMock()

        DockerContainer('url', 'rev', stage, 'ssh_dir', 'inventory_path',
                        'containers_path', 'credentials_path')

        command = DockerContainer.CLIENT().containers.run.call_args.kwargs['command']
        assert command == ['-u', 'url', '-r', 'rev', '-i', '/app/inventory.yml']
