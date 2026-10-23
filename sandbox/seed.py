"""Reset the sandbox, and seed it. Owner: Vipin Mourya.

run.py calls reset() and then seed(task) before every run, with that run's task.
`make app` and `make seed` call seed(None): the demo day. The channel's threads are
emptied before reset() is called, and every message the seed writes on the channel
is marked seeded: the input, not part of the exchange.

seed(task) loads the base data (sandbox/base_data.py) and fixes "now" at
2026-10-21T09:00:00Z, then each of the task's inputs by its kind:

    kind: request (the default)   a message arriving on the channel, and its request:
        {kind: request, thread: SYN-MSG-T04, from: leo.martins@syn-corp.example,
         text: "SYNTHETIC. ...", request_id: SYN-REQ-0001 (optional), at: <time> (optional)}
        A thread starting SYN-HR- is the HR system's feed; any other is an employee's own.
    kind: fault                   the mock identity API fails on purpose:
        {kind: fault, tool: access_grant, times: 2}
    kind: fact                    a small change to the base data, for one task's case:
        {kind: fact, table: employees, id: SYN-EMP-0150, set: {end_date: "2026-10-20"}}

Every record is synthetic and marked; every write is stamped ("seed" here).
"""

from __future__ import annotations

import channel
from sandbox import access, base_data, clock, db, directory, requests

# reset() and seed() touch only files under state_dir() (ours and the channel's), so
# run.py may give each parallel worker a state folder of its own.
PARALLEL_SAFE = True

HR_SENDER = "hr-system@syn-corp.example"
FACT_TABLES = {"employees": {"name", "dept", "title", "role", "manager_id", "employment", "status", "start_date",
                             "end_date"},
               "groups": {"name", "sensitivity", "needs_manager", "protected", "max_days"}}

# The demo day for `make app`: nine requests, six for the workflows and three for the agent.
DEMO = [
    {"thread": "SYN-MSG-D01", "from": "arjun.mehta@syn-corp.example",
     "text": "SYNTHETIC. Hi IT, could I get write access to Jira? I need to move tickets on the Platform board. Arjun"},
    {"thread": "SYN-MSG-D02", "from": "maya.chen@syn-corp.example",
     "text": "SYNTHETIC. Hello, please add me to Salesforce read-only so I can pull the pipeline numbers. Thanks, Maya"},
    {"thread": "SYN-HR-D03", "from": HR_SENDER,
     "text": ("SYNTHETIC. EVENT: new_starter; EMPLOYEE: SYN-EMP-0215; NAME: Nina Kowalski; ROLE: finance_analyst; "
              "MANAGER: SYN-EMP-0040; START: 2026-10-26")},
    {"thread": "SYN-HR-D04", "from": HR_SENDER,
     "text": "SYNTHETIC. EVENT: contract_ended; EMPLOYEE: SYN-EMP-0150; END: 2026-10-20"},
    {"thread": "SYN-MSG-D05", "from": "leo.martins@syn-corp.example",
     "text": ("SYNTHETIC. Hi IT, Priya Nair is on leave for three weeks and I'm covering her month-end reports. "
              "Could you give me the same access she has? Thanks, Leo")},
    {"thread": "SYN-MSG-D06", "from": "arjun.mehta@syn-corp.example",
     "text": "SYNTHETIC. Please add my new teammate Dev Rao to the GitHub engineers group, he started on Monday."},
    {"thread": "SYN-MSG-D07", "from": "felix.wagner@syn-corp.example",
     "text": "SYNTHETIC. hey, can I get admin on AWS production? need to check the campaign landing pages. Felix"},
    {"thread": "SYN-MSG-D08", "from": "grace.okafor@syn-corp.example",
     "text": "SYNTHETIC. Please give me read access to the Data Warehouse for the quarter-end review. Grace Okafor"},
    {"thread": "SYN-MSG-D09", "from": "omar.haddad@syn-corp.example",
     "text": "SYNTHETIC. Could I have Jira edit access? Sales ops tracks the renewals there now. Omar"},
]
DEMO_FACTS = [{"kind": "fact", "table": "employees", "id": "SYN-EMP-0150", "set": {"end_date": "2026-10-20"}}]


def reset() -> None:
    """Empty the sandbox: delete its one file (the harness empties the channel)."""
    db.remove()


def seed(task: dict | None) -> None:
    with db.connect() as conn:
        base_data.load(conn)
    clock.set_now(clock.NOW)
    if task is None:
        items = DEMO_FACTS + DEMO
    else:
        items = task["inputs"] if "inputs" in task else [task["input"]]
    for item in items:
        _seed_one(item)


def _seed_one(item) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"each input is a mapping with a kind (request, fault or fact), not {item!r}")
    kind = item.get("kind", "request")
    if kind == "request":
        _request(item)
    elif kind == "fault":
        access.set_fault(str(item["tool"]), int(item.get("times", 1)))
    elif kind == "fact":
        _fact(item)
    else:
        raise ValueError(f"an input's kind is request, fault or fact, not {kind!r}")


def _request(item: dict) -> None:
    thread, text = str(item["thread"]), str(item["text"])
    sender = str(item.get("from") or "outside")
    source = requests.source_of(thread)
    requester = None
    if source == "employee":
        person = (directory.get_employee(sender) if sender.upper().startswith("SYN-EMP-")
                  else directory.by_email(sender))
        requester = person["id"] if person else None
    channel.receive(thread, text, sender=sender)
    requests.create(thread, source, text, requester_id=requester, request_id=item.get("request_id"),
                    received_at=item.get("at"))


def _fact(item: dict) -> None:
    table, changes = str(item["table"]), dict(item["set"])
    allowed = FACT_TABLES.get(table)
    if allowed is None:
        raise ValueError(f"a fact changes one of {', '.join(FACT_TABLES)}, not {table!r}")
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"a fact on {table} may set {', '.join(sorted(allowed))}; not {', '.join(sorted(unknown))}")
    with db.connect() as conn:
        sets = ", ".join(f"{column} = ?" for column in changes)
        cur = conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*changes.values(), str(item["id"]).upper()))
        if cur.rowcount != 1:
            raise ValueError(f"a fact names {item['id']}, which is not in {table}")
