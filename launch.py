import sys, os, threading, webbrowser, time

if getattr(sys, "frozen", False):
    base_dir = sys._MEIPASS
    os.chdir(os.path.dirname(sys.executable))
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, base_dir)

def open_browser():
    time.sleep(2.5)
    webbrowser.open("http://localhost:5000")

threading.Thread(target=open_browser, daemon=True).start()

from backend.database import init_db
from backend.app import app

init_db()
app.run(debug=False, port=5000, use_reloader=False)