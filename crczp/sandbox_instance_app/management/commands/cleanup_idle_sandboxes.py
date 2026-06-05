from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
import structlog

from crczp.sandbox_common_lib import exceptions
from crczp.sandbox_instance_app.lib import requests as sandbox_requests
from crczp.sandbox_instance_app.models import SandboxLock

LOG = structlog.get_logger()


class Command(BaseCommand):
    help = 'Cleanup sandbox allocation units locked longer than the configured idle threshold.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--idle-minutes',
            type=int,
            default=60,
            help='Cleanup sandboxes whose lock was created at least this many minutes ago.',
        )
        parser.add_argument(
            '--replace',
            action='store_true',
            help='Provision a fresh allocation unit in the same pool after cleanup finishes.',
        )

    def handle(self, *args, **options):
        idle_minutes = options['idle_minutes']
        replace = options['replace']
        cutoff = timezone.now() - timedelta(minutes=idle_minutes)
        locks = SandboxLock.objects.select_related(
            'sandbox__allocation_unit',
            'sandbox__allocation_unit__pool',
            'created_by',
        ).filter(created__lte=cutoff)

        requested = 0
        skipped = 0
        for lock in locks:
            allocation_unit = lock.sandbox.allocation_unit
            try:
                sandbox_requests.create_cleanup_requests(
                    [allocation_unit],
                    force=True,
                    replace=replace,
                )
                requested += 1
            except exceptions.ValidationError as ex:
                skipped += 1
                LOG.warning(
                    'Idle sandbox cleanup skipped',
                    sandbox_lock=lock.id,
                    allocation_unit=allocation_unit.id,
                    reason=str(ex),
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Idle sandbox cleanup requested={requested} skipped={skipped} '
                f'idle_minutes={idle_minutes} replace={replace}'
            )
        )
