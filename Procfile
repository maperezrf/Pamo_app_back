web: python manage.py collectstatic --noinput && python manage.py migrate && gunicorn config.wsgi --bind 0.0.0.0:$PORT --timeout 120
