"""The work waiting, for the app's queue page: open requests and open pending
records. The queue page adds the threads that wait for an outside reply and
anything waiting for approval. Owner: Vipin Mourya.
"""

from __future__ import annotations

from sandbox import pending, requests


def waiting() -> list[dict]:
    items = [{"id": r["id"], "summary": " ".join(r["text"].split())[:90], "status": r["status"],
              "since": r["received_at"], "thread": r["thread_id"]} for r in requests.open_requests()]
    items += [{"id": p["id"], "summary": f"{p['kind'].replace('_', ' ')}"
               + (f" ({p['action']} {p['group_id']})" if p["group_id"] else "")
               + (f" for {p['employee_id']}" if p["employee_id"] else ""),
               "status": "waiting for the operator", "since": p["created_at"], "request": p["request_id"]}
              for p in pending.open_items()]
    return items
