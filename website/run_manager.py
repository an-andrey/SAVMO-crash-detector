import sys
import signal
from subprocess import Popen
import os

from utils.app_utils import clean_dir

UPLOAD_FOLDER = 'static/uploads'
VIDEO_UPLOAD_FOLDER = "videos/user_uploads"
VIDEO_DEMO_FOLDER = "videos/demos"

clean_dir(UPLOAD_FOLDER)
clean_dir(VIDEO_UPLOAD_FOLDER)

gunicorn_cmd = [
    sys.executable, "-m", "gunicorn",
    "-b", "0.0.0.0:8080",
    "-w", "4",
    "--threads", "8",
    "--worker-class", "gthread",
    "--preload", 
    "app:app"
]

print(f"Launching Gunicorn with command: {' '.join(gunicorn_cmd)}")
p = Popen(gunicorn_cmd)

def handle_exit(signum, frame):
    # 1. Kill Gunicorn (The Parent of workers)
    if p.poll() is None:
        print(" -> Terminating Gunicorn...")
        p.terminate()
        try:
            p.wait(timeout=3)
        except:
            print(" -> Gunicorn stuck, forcing kill.")
            p.kill()

    # 2. Kill Orphaned Video/Report Processes
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except:
        pass

    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

p.wait()