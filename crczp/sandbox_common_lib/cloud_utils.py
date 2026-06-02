from crczp.sandbox_common_lib.exceptions import ValidationError
from crczp.sandbox_common_lib.crczp_config import CrczpConfiguration
from crczp.terraform_driver import CrczpTerraformClient, AvailableCloudLibraries, \
    CrczpTerraformBackendType

CLOUD_PROVIDER_OPENSTACK = 'openstack'
CLOUD_PROVIDER_AWS = 'aws'
CLOUD_PROVIDER_AZURE = 'azure'


def _is_aws_configured(crczp_config: CrczpConfiguration) -> bool:
    aws_config = crczp_config.aws
    if aws_config is None:
        return False
    return any(
        [
            aws_config.access_key_id,
            aws_config.secret_access_key,
            aws_config.region,
            aws_config.availability_zone,
        ]
    )


def _is_azure_configured(crczp_config: CrczpConfiguration) -> bool:
    azure_config = crczp_config.azure
    if azure_config is None:
        return False
    return any(
        [
            azure_config.subscription_id,
            azure_config.tenant_id,
            azure_config.client_id,
            azure_config.client_secret,
            azure_config.resource_group_name,
            azure_config.location,
        ]
    )

def get_database_settings(crczp_config: CrczpConfiguration) -> dict:
    db_settings = crczp_config.database
    return {
        'user': db_settings.user,
        'password': db_settings.password,
        'host': db_settings.host,
        'name': db_settings.name,
    }


def get_ostack_client(crczp_config: CrczpConfiguration) -> CrczpTerraformClient:
    """Abstracts creation and authentication to CRCZP lib client."""
    if None in [
        crczp_config.os_auth_url,
        crczp_config.os_application_credential_id,
        crczp_config.os_application_credential_secret,
    ]:
        raise ValidationError(
            "Missing OpenStack configuration options. "
            "Either AWS, Azure, or OpenStack configuration must be set."
        )

    return CrczpTerraformClient(
        auth_url=crczp_config.os_auth_url,
        application_credential_id=crczp_config.os_application_credential_id,
        application_credential_secret=crczp_config.os_application_credential_secret,
        trc=crczp_config.trc, cloud_client=AvailableCloudLibraries.OPENSTACK,
        backend_type=CrczpTerraformBackendType(
            crczp_config.terraform_configuration.backend_type
        ),
        db_configuration=get_database_settings(crczp_config),
        kube_namespace=crczp_config.ansible_runner_settings.namespace,
    )

def get_azure_client(crczp_config: CrczpConfiguration) -> CrczpTerraformClient:
    """
    Get Azure terraform client.
    """
    if crczp_config.azure is None:
        raise ValidationError('Missing Azure configuration options.')

    azure_config = crczp_config.azure
    missing_fields = [
        field_name for field_name, value in {
            'subscription_id': azure_config.subscription_id,
            'tenant_id': azure_config.tenant_id,
            'client_id': azure_config.client_id,
            'client_secret': azure_config.client_secret,
            'resource_group_name': azure_config.resource_group_name,
            'location': azure_config.location,
        }.items() if not value
    ]
    if missing_fields:
        missing_fields_str = ', '.join(missing_fields)
        raise ValidationError(f'Missing Azure configuration options: {missing_fields_str}.')

    return CrczpTerraformClient(
        subscription_id=azure_config.subscription_id,
        tenant_id=azure_config.tenant_id,
        client_id=azure_config.client_id,
        client_secret=azure_config.client_secret,
        resource_group_name=azure_config.resource_group_name,
        location=azure_config.location,
        native_routing=getattr(azure_config, 'native_routing', False),
        omit_router_vms=getattr(azure_config, 'omit_router_vms', False),
        trc=crczp_config.trc,
        cloud_client=AvailableCloudLibraries.AZURE,
        backend_type=CrczpTerraformBackendType(
            crczp_config.terraform_configuration.backend_type
        ),
        db_configuration=get_database_settings(crczp_config),
        kube_namespace=crczp_config.ansible_runner_settings.namespace,
    )

def get_configured_cloud_provider(crczp_config: CrczpConfiguration) -> str:
    """
    Resolve which cloud provider should be used for this configuration.

    OpenStack remains the default path for backwards compatibility when neither AWS nor Azure
    sections are configured.
    """
    configured = [
        provider_name for provider_name, is_configured in [
            (CLOUD_PROVIDER_AWS, _is_aws_configured(crczp_config)),
            (CLOUD_PROVIDER_AZURE, _is_azure_configured(crczp_config)),
        ] if is_configured
    ]

    if len(configured) > 1:
        raise ValidationError('Only one cloud provider configuration can be set at a time.')

    if configured:
        return configured[0]

    return CLOUD_PROVIDER_OPENSTACK

def get_terraform_client_for_config(crczp_config: CrczpConfiguration) -> CrczpTerraformClient:
    """
    Get a Terraform client for the configured cloud provider.
    """
    provider = get_configured_cloud_provider(crczp_config)
    if provider == CLOUD_PROVIDER_AWS:
        return get_aws_client(crczp_config)
    if provider == CLOUD_PROVIDER_AZURE:
        return get_azure_client(crczp_config)
    return get_ostack_client(crczp_config)

def provider_supports_x509_keypair(provider: str) -> bool:
    """
    Return whether the selected provider supports the legacy x509 management certificate flow.
    """
    return provider == CLOUD_PROVIDER_OPENSTACK

def get_aws_client(crczp_config: CrczpConfiguration) -> CrczpTerraformClient:
    """
    Get AWS terraform client
    """
    return CrczpTerraformClient(
        aws_access_key=crczp_config.aws.access_key_id,
        aws_secret_key=crczp_config.aws.secret_access_key,
        region=crczp_config.aws.region,
        availability_zone=crczp_config.aws.availability_zone,
        base_vpc_name=crczp_config.aws.base_vpc,
        base_subnet_name=crczp_config.aws.base_subnet,
        trc=crczp_config.trc, cloud_client=AvailableCloudLibraries.AWS,
        backend_type=CrczpTerraformBackendType(
            crczp_config.terraform_configuration.backend_type
        ),
        db_configuration=get_database_settings(crczp_config),
        kube_namespace=crczp_config.ansible_runner_settings.namespace,
    )
