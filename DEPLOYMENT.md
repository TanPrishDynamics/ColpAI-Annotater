# ColpAI — Deploying Live on a Cloud VM

This puts ColpAI online so doctors log in from anywhere, annotate, and an admin
downloads the results — over HTTPS, on a Linux server you control (DigitalOcean,
AWS EC2, Hetzner, etc.).

> ⚠️ **These are patient images.** Use HTTPS (covered below), strong passwords,
> and give logins only to the specific doctors. Check whether your jurisdiction
> (HIPAA / GDPR / local rules) restricts where such data may be hosted — some
> clinics must keep it on-premise or in-country.

Assumes Ubuntu 22.04+. Replace `colpai.yourdomain.com` with your real domain and
`/opt/colpai` if you prefer another path.

---

## 1. Provision the server

- Create a VM (2 vCPU / 2–4 GB RAM is plenty for a small team).
- Point an **A record** for `colpai.yourdomain.com` at the VM's public IP.
- SSH in and create a dedicated service user:

```bash
sudo adduser --system --group colpai
sudo mkdir -p /opt/colpai && sudo chown colpai:colpai /opt/colpai
```

## 2. System dependencies

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip nginx git
```

## 3. Get the code + virtualenv

```bash
sudo -u colpai bash
cd /opt/colpai
git clone <your-repo-url> ColpAi-Annotater      # or scp the folder up
cd ColpAi-Annotater
python3 -m venv venv
./venv/bin/pip install -r deploy/requirements-prod.txt
```

## 4. Database

The app uses SQLite by default (fine for a small team). Create the schema:

```bash
COLPAI_CONFIG=prod COLPAI_SECRET_KEY=temp ./venv/bin/flask --app wsgi db upgrade
```

> Moving to Postgres later? Set `COLPAI_DATABASE_URI` (see `colpai.env.example`)
> and run `flask db upgrade` again.

## 5. Create the first admin login

```bash
./venv/bin/python -m scripts.create_user --username admin --role admin --full-name "Site Admin"
# prompts for a password
```

You'll use this to create all the doctor logins later from the web Admin panel —
no more command line needed.

## 6. Environment / secret key

```bash
cp deploy/colpai.env.example /opt/colpai/colpai.env
# generate a strong secret:
./venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
# paste it into COLPAI_SECRET_KEY in the file, then lock it down:
chmod 600 /opt/colpai/colpai.env
exit   # back to your sudo user
```

The app **refuses to start in prod** without a real `COLPAI_SECRET_KEY`.

## 7. Run it as a service (gunicorn + systemd)

```bash
sudo cp deploy/colpai.service /etc/systemd/system/colpai.service
# check the paths/user inside match your setup, then:
sudo systemctl daemon-reload
sudo systemctl enable --now colpai
sudo systemctl status colpai          # should be "active (running)"
journalctl -u colpai -f               # live logs
```

At this point the app is running on `127.0.0.1:8000` (not public yet).

## 8. nginx + HTTPS

```bash
sudo cp deploy/nginx-colpai.conf /etc/nginx/sites-available/colpai
# edit server_name + the /static/ alias path if different
sudo ln -s /etc/nginx/sites-available/colpai /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# free HTTPS certificate (auto-renews):
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d colpai.yourdomain.com
```

Visit **https://colpai.yourdomain.com** → you should see the login page.

## 9. Get the images onto the server

The DB stores each image's file path, and the server serves files from disk, so
the images must live **on the VM**. Upload them, then register them:

```bash
# from your Mac, push the images up:
rsync -avz /path/to/images/ colpai@<vm-ip>:/opt/colpai/images/

# on the server, register them in the DB:
cd /opt/colpai/ColpAi-Annotater
./venv/bin/python -m scripts.ingest_images --root /opt/colpai/images --dataset my_dataset
```

> This is also where your **Google Drive** idea fits: install `rclone` on the VM,
> `rclone sync your-drive:colpai_images /opt/colpai/images`, then re-run the
> ingest command (or cron it) to pick up new uploads.

## 10. Create doctor logins (web, no CLI)

1. Log in as the admin → click **Admin** in the top bar.
2. **Add a doctor / user**: enter a username, full name, a temporary password,
   role = *Annotator*. Click **Create login**.
3. Share the username + password with that doctor. They log in at the same URL
   and start annotating immediately.

Repeat per doctor — each gets their own login, and their work is attributed to
them. You can **disable** or **reset the password** for any account from the
same table, and watch each doctor's **submitted / reviewed / draft** counts.

## 11. Download annotations

Admin panel → **Download annotations**: pick a dataset + which annotations to
include, then:

- **Full bundle** — zip of original images + annotation overlays + CSV/COCO labels.
- **CSV / COCO / YOLO / Masks** — individual label formats for model training.

(Reviewers can also export from the Dashboard; user management is admin-only.)

---

## Day-to-day operations

| Task | Command |
|---|---|
| View logs | `journalctl -u colpai -f` |
| Restart after a code update | `git pull && sudo systemctl restart colpai` |
| Apply new DB migrations | `./venv/bin/flask --app wsgi db upgrade` |
| **Back up everything** | copy `data/annotations.db*` and `/opt/colpai/images/` |
| Add more images later | `ingest_images --root ... --dataset ...` |

**Back up the database regularly** — it holds every annotation. A daily cron
copying `data/annotations.db` off the box is enough to start. Example:

```bash
0 2 * * *  cp /opt/colpai/ColpAi-Annotater/data/annotations.db /opt/colpai/backups/annotations-$(date +\%F).db
```

## Scaling notes

- SQLite (WAL mode, already enabled) comfortably handles a handful of concurrent
  doctors. If you grow to many simultaneous annotators, switch to Postgres via
  `COLPAI_DATABASE_URI` and bump `COLPAI_WORKERS`.
- Big bundle exports stream for a while; the gunicorn + nginx timeouts are set to
  300s. Raise them in `deploy/gunicorn.conf.py` / `deploy/nginx-colpai.conf` if
  your dataset is very large.

  ## too run  
  flask --app wsgi run --port 5004 --debug