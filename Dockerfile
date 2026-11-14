FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY frontend-pkg /app/frontend-pkg
COPY backend /app/backend
WORKDIR /app/backend
RUN pip install --no-cache-dir -r requirements-lock.txt && \
    useradd --create-home app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "python manage.py collectstatic --noinput && exec gunicorn mysite.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 60 --access-logfile -"]
