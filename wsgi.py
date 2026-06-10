"""WSGI entrypoint. Used by `python wsgi.py` and any prod WSGI server."""
import socket
from app import create_app

app = create_app()


def _get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


if __name__ == '__main__':
    port = 5004
    local_ip = _get_local_ip()
    print(f"\n  Local:   http://127.0.0.1:{port}")
    print(f"  Network: http://{local_ip}:{port}  (share this on your WiFi)\n")
    app.run(host='0.0.0.0', port=port, debug=True)
