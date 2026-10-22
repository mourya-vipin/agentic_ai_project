"""The web app: your product's own pages, and the operator's pages around the agent.

    app/main.py       the app, and the operator's pages: /console, /queue, /threads,
                      /approvals, /traces. Keep their contract: each reaches the agent
                      only through harness.turns, which calls agent/service.py.
    app/product.py    your product's own pages, starting at /: the features your
                      user needs with no agent at all, reading and writing the domain
                      data through sandbox/ modules.
    app/templates/    the pages' HTML (Jinja2). base.html holds the menu and the style.

Serve it with `make app`, at http://localhost:8000.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
