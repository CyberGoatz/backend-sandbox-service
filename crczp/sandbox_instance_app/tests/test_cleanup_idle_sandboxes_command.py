from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

pytestmark = pytest.mark.django_db


def test_cleanup_idle_sandboxes_requests_cleanup_for_expired_locks(mocker, sandbox_lock):
    sandbox_lock.created = timezone.now() - timedelta(minutes=61)
    sandbox_lock.save()
    fake_create_cleanup = mocker.patch(
        'crczp.sandbox_instance_app.management.commands.cleanup_idle_sandboxes.'
        'sandbox_requests.create_cleanup_requests'
    )
    stdout = StringIO()

    call_command('cleanup_idle_sandboxes', '--idle-minutes', '60', '--replace', stdout=stdout)

    fake_create_cleanup.assert_called_once_with(
        [sandbox_lock.sandbox.allocation_unit],
        force=True,
        replace=True,
    )
    assert 'requested=1 skipped=0' in stdout.getvalue()


def test_cleanup_idle_sandboxes_ignores_recent_locks(mocker, sandbox_lock):
    sandbox_lock.created = timezone.now() - timedelta(minutes=30)
    sandbox_lock.save()
    fake_create_cleanup = mocker.patch(
        'crczp.sandbox_instance_app.management.commands.cleanup_idle_sandboxes.'
        'sandbox_requests.create_cleanup_requests'
    )

    call_command('cleanup_idle_sandboxes', '--idle-minutes', '60')

    fake_create_cleanup.assert_not_called()
