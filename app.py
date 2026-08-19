"""Root-level Flask entrypoint, required by Vercel's Python auto-detection
(it only looks in default root locations, not redgold/webapp.py)."""
from redgold.webapp import app

if __name__ == "__main__":
    app.run()
