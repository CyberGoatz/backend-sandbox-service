from types import SimpleNamespace

import pytest

from crczp.sandbox_common_lib import cloud_utils
from crczp.sandbox_common_lib.exceptions import ValidationError
from crczp.terraform_driver import AvailableCloudLibraries


def _build_config(*, aws=None, azure=None):
	return SimpleNamespace(
		aws=aws,
		azure=azure,
		trc='trc',
		terraform_configuration=SimpleNamespace(backend_type='local'),
		ansible_runner_settings=SimpleNamespace(namespace='crczp'),
		database=SimpleNamespace(user='user', password='password', host='host', name='name'),
	)


def test_get_configured_cloud_provider_defaults_to_openstack():
	config = _build_config()

	assert cloud_utils.get_configured_cloud_provider(config) == cloud_utils.CLOUD_PROVIDER_OPENSTACK


def test_get_configured_cloud_provider_returns_aws():
	config = _build_config(aws=SimpleNamespace())

	assert cloud_utils.get_configured_cloud_provider(config) == cloud_utils.CLOUD_PROVIDER_AWS


def test_get_configured_cloud_provider_returns_azure():
	config = _build_config(azure=SimpleNamespace())

	assert cloud_utils.get_configured_cloud_provider(config) == cloud_utils.CLOUD_PROVIDER_AZURE


def test_get_configured_cloud_provider_rejects_multiple_explicit_providers():
	config = _build_config(aws=SimpleNamespace(), azure=SimpleNamespace())

	with pytest.raises(ValidationError):
		cloud_utils.get_configured_cloud_provider(config)


def test_provider_supports_x509_keypair_only_for_openstack():
	assert cloud_utils.provider_supports_x509_keypair(cloud_utils.CLOUD_PROVIDER_OPENSTACK) is True
	assert cloud_utils.provider_supports_x509_keypair(cloud_utils.CLOUD_PROVIDER_AWS) is False
	assert cloud_utils.provider_supports_x509_keypair(cloud_utils.CLOUD_PROVIDER_AZURE) is False


def test_get_azure_client_uses_azure_provider(mocker):
	terraform_client = mocker.patch('crczp.sandbox_common_lib.cloud_utils.CrczpTerraformClient')
	config = _build_config(
		azure=SimpleNamespace(
			subscription_id='sub',
			tenant_id='tenant',
			client_id='client',
			client_secret='secret',
			resource_group_name='rg',
			location='westeurope',
		)
	)

	cloud_utils.get_azure_client(config)

	terraform_client.assert_called_once_with(
		subscription_id='sub',
		tenant_id='tenant',
		client_id='client',
		client_secret='secret',
		resource_group_name='rg',
		location='westeurope',
		trc='trc',
		cloud_client=AvailableCloudLibraries.AZURE,
		backend_type=mocker.ANY,
		db_configuration={'user': 'user', 'password': 'password', 'host': 'host', 'name': 'name'},
		kube_namespace='crczp',
	)


def test_get_azure_client_requires_all_azure_fields():
	config = _build_config(
		azure=SimpleNamespace(
			subscription_id='sub',
			tenant_id='tenant',
			client_id='client',
			client_secret='',
			resource_group_name='rg',
			location='westeurope',
		)
	)

	with pytest.raises(ValidationError, match='client_secret'):
		cloud_utils.get_azure_client(config)
