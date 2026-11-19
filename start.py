"""Start Alma using its isolated dependencies on macOS, Windows, or Linux."""
import os
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    """Create the interface environment if needed, then serve it on loopback."""
    if sys.version_info < (3, 11):
        raise SystemExit("Instalá Python 3.11 o 3.12 para abrir Alma.")
    os.chdir(ROOT)
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        if not python.exists():
            print("Preparando el entorno de Alma…", flush=True)
            venv.create(ROOT / ".venv", with_pip=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)
        raise SystemExit(subprocess.call([str(python), str(Path(__file__).resolve())]))
    port = int(os.environ.get("ALMA_PORT", "7864"))
    url = f"http://127.0.0.1:{port}"

    def open_when_ready():
        for _ in range(60):
            try:
                with urllib.request.urlopen(url + "/api/health", timeout=1):
                    webbrowser.open(url)
                    return
            except OSError:
                time.sleep(1)
    threading.Thread(target=open_when_ready, daemon=True).start()
    print(f"Alma: {url}\nAbrí ACE-Step en http://127.0.0.1:7860 para generar música.", flush=True)
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
