# ColpAI Annotater — Installation

Local setup. Copy-paste the commands in order.

**Need:** Python 3.10+ and Git.

---

## Windows (PowerShell)

```powershell
git clone git@github.com:TanPrishDynamics/ColpAI-Annotater.git
cd ColpAI-Annotater
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app wsgi db upgrade
python -m scripts.create_user --username admin --role admin --full-name "Site Admin"
flask --app wsgi run --port 5004 --debug
```

## macOS / Linux

```bash
git clone git@github.com:TanPrishDynamics/ColpAI-Annotater.git
cd ColpAI-Annotater
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
flask --app wsgi db upgrade
python -m scripts.create_user --username admin --role admin --full-name "Site Admin"
flask --app wsgi run --port 5004 --debug
```

`create_user` asks for a password — type one and remember it.

Open **http://127.0.0.1:5004** and log in as `admin`.

---

## Load images

```bash
python -m scripts.ingest_images --root "path/to/images" --dataset my_dataset
```

Supported: `.jpg .jpeg .png .bmp .tif .tiff`

---

## Notes

- Run the app from inside the venv (`Activate` first), or `flask`/`python` won't be found.
- Forgot the admin password? Reset it:
  ```bash
  python -m scripts.create_user --username admin2 --role admin
  ```
- HTTPS clone instead of SSH: `git clone https://github.com/TanPrishDynamics/ColpAI-Annotater.git`
- Going live for a team → see [DEPLOYMENT.md](DEPLOYMENT.md). How to annotate → [ANNOTATION_GUIDE.md](ANNOTATION_GUIDE.md).
