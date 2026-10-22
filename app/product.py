"""Your product's own pages. Replace the placeholder with your system's features
that need no agent: the records your agent works on, and a way to do the core
task by hand. They read and write your domain data through your sandbox/ modules,
the same modules your agent's tools wrap; they never import the agent. A write
made by hand here has no run and no console session: stamp it with a marker of
your own, such as manual. A reply the operator sends by hand from here, such as a
pending reply your system drafted, goes out as the operator's:
channel.send(thread_id, text, sender="operator").

Add routes to `router`; app/main.py includes it. Put their templates in
app/templates/ and extend base.html, so the menu stays.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app import templates

router = APIRouter()


@router.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {"page": "home"})
