# ColpAI Annotater — Installation Guide

This guide gets ColpAI running **on your own machine** for development, testing,
or a small local trial. To put it online for a team, see [DEPLOYMENT.md](DEPLOYMENT.md).

> ⚠️ ColpAI handles **patient images**. Even locally, use real passwords and keep
> the `data/` folder off shared/synced drives if your rules require it.

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.10+** | Check with `python --version` (Windows) or `python3 --version` (macOS/Linux). |
| **pip** | Ships with Python. |
| **Git** | To clone the repo (or download the ZIP). |

No database server is needed — ColpAI defaults to a local **SQLite** file.

---

## 2. Get the code

```bash
git clone git@github.com:TanPrishDynamics/ColpAI-Annotater.git
cd ColpAI-Annotater
```

> Using HTTPS instead of SSH? Clone with
> `git clone https://github.com/TanPrishDynamics/ColpAI-Annotater.git`

---

## 3. Create a virtual environment

Keeps ColpAI's dependencies isolated from the rest of your system.

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

Your prompt should now show `(venv)`.

---

## 4. Install dependencies

```bash
pip install -r requirements.txt
```

This installs Flask, SQLAlchemy, Pillow, numpy, and the rest (see
[requirements.txt](requirements.txt)).

---

## 5. Configure environment variables

Copy the template and adjust if needed:

**Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
```

**macOS / Linux:**
```bash
cp .env.example .env
```

The defaults in [.env.example](.env.example) work out of the box for local use:

| Variable | Default | Purpose |
|---|---|---|
| `COLPAI_CONFIG` | `dev` | Config profile: `dev` / `test` / `prod`. |
| `COLPAI_SECRET_KEY` | `dev-secret-change-me-in-prod` | Session signing key. Fine for dev; **must** be replaced in prod. |
| `COLPAI_DATABASE_URI` | `sqlite:///data/annotations.db` | Local SQLite by default. |
| `COLPAI_UPLOAD_DIR` | `data/uploads` | Where ingested/uploaded images are stored. |
| `COLPAI_MAX_UPLOAD_MB` | `200` | Per-request upload cap. |

> The app loads `dev` config by default, so you can skip the `.env` entirely for a
> quick local run. The `data/` directory is created automatically on first boot.

---

## 6. Create the database schema

Run the migrations to build the SQLite tables:

```bash
flask --app wsgi db upgrade
```

This creates `data/annotations.db`.

---

## 7. Create your first admin user

```bash
python -m scripts.create_user --username admin --role admin --full-name "Site Admin"
```

You'll be prompted for a password. Roles are `admin`, `reviewer`, or `annotator`.
Once logged in as admin, you can create the rest of the users from the web
**Admin** panel — no command line needed.

---

## 8. Run the app

```bash
flask --app wsgi run --port 5004 --debug
```

or equivalently:

```bash
python wsgi.py
```

You'll see:

```
  Local:   http://127.0.0.1:5004
  Network: http://<your-ip>:5004   (share this on your WiFi)
```

Open **http://127.0.0.1:5004** and log in with the admin account from step 7.
The **Network** URL lets others on the same WiFi reach your instance.

---

## 9. Load some images

The database stores each image's path; the app serves the files from disk. Point
the ingest script at a folder of images:

```bash
python -m scripts.ingest_images --root "path/to/images" --dataset my_dataset
```

Add `--dry-run` first to preview counts without writing to the DB. Supported
formats: `.jpg`, `.jpeg`, `.png`, `.bmp`, `.tif`, `.tiff`.

Images now appear in the annotation queue. See [ANNOTATION_GUIDE.md](ANNOTATION_GUIDE.md)
for how to annotate, review, and export.

---

## 10. (Optional) Run the tests

```bash
pytest
```

The test suite uses an in-memory SQLite database and won't touch your dev data.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `flask: command not found` | Activate the venv (step 3); `flask` lives inside it. |
| `Activate.ps1 cannot be loaded` (Windows) | Run `Set-ExecutionPolicy -Scope Process RemoteSigned`, then re-activate. |
| App refuses to boot complaining about the secret key | You set `COLPAI_CONFIG=prod` without a real `COLPAI_SECRET_KEY`. Use `dev` locally, or generate a key: `python -c "import secrets; print(secrets.token_hex(32))"`. |
| Port 5004 already in use | Run with a different `--port`. |
| Images don't show up after ingest | Confirm the `--root` path is correct and the files have a supported extension; re-run without `--dry-run`. |

---

## Next steps

- **Annotating / reviewing / exporting:** [ANNOTATION_GUIDE.md](ANNOTATION_GUIDE.md)
- **Putting it online for a team (HTTPS, gunicorn, nginx):** [DEPLOYMENT.md](DEPLOYMENT.md)
