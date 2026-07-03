#!/usr/bin/env sh

LISTEN_IP=0.0.0.0
LISTEN_PORT=8000
GUNICORN_WORKERS=${GUNICORN_WORKERS:-3}

CREATE_SUPERUSER="
from django.contrib.auth.models import User
from django.db.utils import IntegrityError
try:
    User.objects.create_superuser('${DJANGO_ADMIN_USER}', '${DJANGO_ADMIN_EMAIL}', '${DJANGO_ADMIN_PASSWORD}')
except IntegrityError:
    print('superuser \'${DJANGO_ADMIN_USER}\' already exists')
"

set -e

echo "Running Django migrations"
python manage.py migrate
python manage.py createcachetable
echo "Ensuring admin user exists"
python manage.py shell << EOF
${CREATE_SUPERUSER}
EOF
echo "Registering roles in user-and-group"
python manage.py register_roles
echo "Starting gunicorn on ${LISTEN_IP}:${LISTEN_PORT} with ${GUNICORN_WORKERS} workers"
gunicorn --bind ${LISTEN_IP}:${LISTEN_PORT} --timeout 600 --workers "${GUNICORN_WORKERS}" crczp.sandbox_service_project.wsgi:application
