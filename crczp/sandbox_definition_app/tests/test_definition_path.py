import pytest

from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_common_lib.crczp_config import CrczpConfiguration
from crczp.sandbox_definition_app import serializers
from crczp.sandbox_definition_app.lib import definitions
from crczp.sandbox_definition_app.lib.paths import normalize_definition_path
from crczp.sandbox_definition_app.models import Definition

pytestmark = pytest.mark.django_db

CFG = CrczpConfiguration()
VARIABLES_YML = 'var1:\n  type: text\n'


class FakeCache:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class TestNormalizeDefinitionPath:
    @pytest.mark.parametrize('path, expected', [
        ('', ''),
        (None, ''),
        ('   ', ''),
        ('labs', 'labs'),
        ('labs/course-1/lab_2.x', 'labs/course-1/lab_2.x'),
        ('labs/lab/', 'labs/lab'),
        ('  labs/lab//  ', 'labs/lab'),
        ('/labs/lab', 'labs/lab'),
        ('//labs/lab/', 'labs/lab'),
    ])
    def test_valid(self, path, expected):
        assert normalize_definition_path(path) == expected

    @pytest.mark.parametrize('path', [
        '../labs', 'labs/../lab', 'labs/..', '..',
        'C:/labs', 'labs/lab name', 'labs/lab;rm', 'labs\\lab', 'labs/ž', 'labs/lab?x',
    ])
    def test_invalid(self, path):
        with pytest.raises(exceptions.ValidationError):
            normalize_definition_path(path)

    def test_dots_inside_segment_allowed(self):
        assert normalize_definition_path('labs/a..b') == 'labs/a..b'


@pytest.fixture
def provider(mocker):
    provider = mocker.MagicMock()
    provider.get_rev_sha.return_value = 'sha'
    provider.get_file.return_value = 'content'
    mocker.patch('crczp.sandbox_definition_app.lib.definitions.get_def_provider', return_value=provider)
    return provider


class TestPathPrefixing:
    def test_get_definition(self, mocker, provider):
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.load_definition')
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.caches', {'topology_cache': FakeCache()})
        definitions.get_definition('url', 'rev', CFG, 'labs/lab-1')
        provider.get_file.assert_called_once_with('labs/lab-1/topology.yml', 'sha')

    def test_get_definition_without_path_reads_root(self, mocker, provider):
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.load_definition')
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.caches', {'topology_cache': FakeCache()})
        definitions.get_definition('url', 'rev', CFG)
        provider.get_file.assert_called_once_with('topology.yml', 'sha')

    def test_get_containers(self, mocker, provider):
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.load_docker_containers')
        definitions.get_containers('url', 'rev', CFG, 'labs/lab-1')
        provider.get_file.assert_called_once_with('labs/lab-1/containers.yml', 'rev')

    def test_get_variables(self, provider):
        provider.get_file.return_value = VARIABLES_YML
        definitions.get_variables('url', 'rev', CFG, 'labs/lab-1')
        provider.get_file.assert_called_once_with('labs/lab-1/variables.yml', 'rev')

    def test_get_variables_without_path_reads_root(self, provider):
        provider.get_file.return_value = VARIABLES_YML
        definitions.get_variables('url', 'rev', CFG)
        provider.get_file.assert_called_once_with('variables.yml', 'rev')

    def test_get_dockerfile(self, provider):
        definitions.get_dockerfile('url', 'rev', CFG, 'containers/web', 'labs/lab-1')
        provider.get_file.assert_called_once_with('labs/lab-1/containers/web/Dockerfile', 'rev')

    def test_get_dockerfile_without_path_reads_root(self, provider):
        definitions.get_dockerfile('url', 'rev', CFG, 'containers/web')
        provider.get_file.assert_called_once_with('containers/web/Dockerfile', 'rev')

    def test_validate_docker_containers_passes_path(self, mocker):
        get_definition = mocker.patch('crczp.sandbox_definition_app.lib.definitions.get_definition')
        get_containers = mocker.patch('crczp.sandbox_definition_app.lib.definitions.get_containers',
                                      return_value=None)
        definitions.validate_docker_containers('url', 'rev', CFG, 'labs/lab-1')
        get_definition.assert_called_once_with('url', 'rev', CFG, 'labs/lab-1')
        get_containers.assert_called_once_with('url', 'rev', CFG, 'labs/lab-1')


class TestCacheKeys:
    def test_cache_keys_differ_by_path(self, mocker, provider):
        cache = FakeCache()
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.caches', {'topology_cache': cache})
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.load_definition',
                     side_effect=lambda stream: object())
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')

        first = definitions.get_definition('url', 'rev', CFG, 'labs/a')
        second = definitions.get_definition('url', 'rev', CFG, 'labs/b')
        root = definitions.get_definition('url', 'rev', CFG)

        assert len(cache.data) == 3
        assert first is not second
        assert root is not first
        assert provider.get_file.call_count == 3

    def test_cache_hit_for_same_path(self, mocker, provider):
        cache = FakeCache()
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.caches', {'topology_cache': cache})
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.load_definition',
                     side_effect=lambda stream: object())
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')

        first = definitions.get_definition('url', 'rev', CFG, 'labs/a')
        again = definitions.get_definition('url', 'rev', CFG, 'labs/a')

        assert first is again
        assert provider.get_file.call_count == 1


class TestUniqueness:
    URL = 'https://gitlab.example.com/trainings.git'
    REV = 'main'

    def _serializer(self, path, name='def'):
        return serializers.DefinitionSerializerCreate(
            data=dict(name=name, url=self.URL, rev=self.REV, path=path))

    def test_two_paths_same_url_and_rev_allowed(self, created_by):
        for path in ('labs/a', 'labs/b', ''):
            serializer = self._serializer(path)
            assert serializer.is_valid(), serializer.errors
            serializer.save(created_by=created_by)
        assert Definition.objects.filter(url=self.URL, rev=self.REV).count() == 3

    def test_same_url_rev_path_rejected(self, created_by):
        serializer = self._serializer('labs/a')
        assert serializer.is_valid()
        serializer.save(created_by=created_by)

        duplicate = self._serializer('labs/a')
        assert not duplicate.is_valid()
        assert "code='unique'" in str(duplicate.errors)

    def test_path_normalised_before_uniqueness(self, created_by):
        serializer = self._serializer('labs/a')
        assert serializer.is_valid()
        serializer.save(created_by=created_by)
        assert not self._serializer('/labs/a/').is_valid()
        assert not self._serializer(' labs/a/ ').is_valid()

    def test_omitted_path_defaults_to_root(self, created_by):
        serializer = serializers.DefinitionSerializerCreate(data=dict(name='d', url=self.URL, rev=self.REV))
        assert serializer.is_valid(), serializer.errors
        assert serializer.save(created_by=created_by).path == ''

    @pytest.mark.parametrize('path', ['../x', 'a/../b', 'bad char'])
    def test_invalid_path_rejected(self, path):
        serializer = self._serializer(path)
        assert not serializer.is_valid()
        assert 'path' in serializer.errors


class TestCreateDefinitionWithPath:
    URL = 'https://gitlab.example.com/trainings.git'
    REV = 'main'

    @pytest.fixture(autouse=True)
    def setup(self, mocker):
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.DefinitionProvider')
        mocker.patch('crczp.terraform_driver.CrczpTerraformClient.validate_topology_definition')
        mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
        self.validate_containers = mocker.patch(
            'crczp.sandbox_definition_app.lib.definitions.validate_docker_containers')
        top_def = mocker.Mock()
        top_def.name = 'def-name'
        self.get_definition = mocker.patch('crczp.sandbox_definition_app.lib.definitions.get_definition',
                                           return_value=top_def)

    def test_path_stored_normalised_and_used(self, created_by):
        definition = definitions.create_definition(self.URL, created_by, self.REV, '/labs/a/')
        assert definition.path == 'labs/a'
        self.get_definition.assert_called_once_with(self.URL, self.REV, definitions.settings.CRCZP_CONFIG, 'labs/a')
        self.validate_containers.assert_called_once_with(
            self.URL, self.REV, definitions.settings.CRCZP_CONFIG, 'labs/a')

    def test_two_paths_allowed_duplicate_rejected(self, created_by):
        definitions.create_definition(self.URL, created_by, self.REV, 'labs/a')
        definitions.create_definition(self.URL, created_by, self.REV, 'labs/b')
        with pytest.raises(exceptions.ValidationError, match='already exists'):
            definitions.create_definition(self.URL, created_by, self.REV, 'labs/a')

    def test_invalid_path_rejected_before_git_access(self, created_by):
        with pytest.raises(exceptions.ValidationError):
            definitions.create_definition(self.URL, created_by, self.REV, '../x')
        self.get_definition.assert_not_called()
