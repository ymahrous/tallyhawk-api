import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

celery_app = Celery(
    "tallyhawk_worker",
    broker=f"rediss://default:{os.getenv('UPSTASH_REDIS_PASSWORD')}@{os.getenv('UPSTASH_REDIS_ENDPOINT')}",
    include=['tasks'] 
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    # Simplified SSL config
    broker_use_ssl={
        'ssl_cert_reqs': None
    }
)