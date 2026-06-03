"""Gunicorn config for ColpAI. Run with:  gunicorn -c deploy/gunicorn.conf.py wsgi:app"""
import multiprocessing
import os

# Bind to a local port; nginx proxies HTTPS traffic to it.
bind = os.environ.get('COLPAI_BIND', '127.0.0.1:8000')

# A safe default worker count. SQLite + WAL handles a small clinical team fine;
# if you move to Postgres you can raise this.
workers = int(os.environ.get('COLPAI_WORKERS', (multiprocessing.cpu_count() * 2) + 1))

# Bundle/image exports can take a while on big datasets -- don't kill them early.
timeout = int(os.environ.get('COLPAI_TIMEOUT', 300))

accesslog = '-'   # stdout -> journald
errorlog = '-'
loglevel = os.environ.get('COLPAI_LOGLEVEL', 'info')
