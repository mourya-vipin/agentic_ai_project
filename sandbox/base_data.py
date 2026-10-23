"""The base data: Synthetic Corp's directory, systems, groups, role policy, current
memberships and fake credentials. Generated the same way every time (a fixed
random seed), so every run starts from the same company. Every record is
synthetic; every secret starts FAKE-. Owner: Vipin Mourya.

The named people at the top are the ones our test tasks rely on: their IDs are
part of the team's contract, so change them only with the task owner.
"""

from __future__ import annotations

import hashlib
import random

SEED = 9
COMPANY_SIZE = 250
SEEDED = "seed"            # the stamp and actor of everything written here

SYSTEMS = [  # id, name, owner department, sensitivity
    ("SYN-SYS-SSO", "Single sign-on", "IT", "medium"),
    ("SYN-SYS-SLACK", "Slack", "IT", "low"),
    ("SYN-SYS-JIRA", "Jira", "Engineering", "low"),
    ("SYN-SYS-CONFLUENCE", "Confluence", "IT", "low"),
    ("SYN-SYS-GITHUB", "GitHub", "Engineering", "medium"),
    ("SYN-SYS-VPN", "VPN", "IT", "medium"),
    ("SYN-SYS-SALESFORCE", "Salesforce", "Sales", "medium"),
    ("SYN-SYS-FINREP", "Finance Reports", "Finance", "medium"),
    ("SYN-SYS-DWH", "Data Warehouse", "Data", "medium"),
    ("SYN-SYS-PAYROLL", "Payroll", "People", "high"),
    ("SYN-SYS-AWSPROD", "AWS Production", "Engineering", "high"),
    ("SYN-SYS-LEGAL", "Legal hold archive", "Legal", "medium"),
    ("SYN-SYS-PORTAL", "Contractor portal", "IT", "low"),
]

GROUPS = [  # id, system, name, level, sensitivity, needs_manager, protected, max_days
    ("SYN-GRP-SLACK-MEMBER", "SYN-SYS-SLACK", "Slack members", "write", "low", 0, 0, None),
    ("SYN-GRP-JIRA-READ", "SYN-SYS-JIRA", "Jira readers", "read", "low", 0, 0, None),
    ("SYN-GRP-JIRA-WRITE", "SYN-SYS-JIRA", "Jira editors", "write", "low", 0, 0, None),
    ("SYN-GRP-JIRA-ADMIN", "SYN-SYS-JIRA", "Jira administrators", "admin", "medium", 1, 0, None),
    ("SYN-GRP-CONFLUENCE-READ", "SYN-SYS-CONFLUENCE", "Confluence readers", "read", "low", 0, 0, None),
    ("SYN-GRP-CONFLUENCE-ENG", "SYN-SYS-CONFLUENCE", "Confluence engineering space", "write", "low", 0, 0, None),
    ("SYN-GRP-CONFLUENCE-FINANCE", "SYN-SYS-CONFLUENCE", "Confluence finance space", "write", "medium", 0, 0, None),
    ("SYN-GRP-GITHUB-READ", "SYN-SYS-GITHUB", "GitHub readers", "read", "medium", 0, 0, None),
    ("SYN-GRP-GITHUB-ENG", "SYN-SYS-GITHUB", "GitHub engineers", "write", "medium", 0, 0, None),
    ("SYN-GRP-GITHUB-ADMIN", "SYN-SYS-GITHUB", "GitHub organisation admins", "admin", "high", 1, 0, None),
    ("SYN-GRP-VPN-USER", "SYN-SYS-VPN", "VPN users", "write", "medium", 0, 0, None),
    ("SYN-GRP-SALESFORCE-READ", "SYN-SYS-SALESFORCE", "Salesforce readers", "read", "medium", 0, 0, None),
    ("SYN-GRP-SALESFORCE-EDIT", "SYN-SYS-SALESFORCE", "Salesforce editors", "write", "medium", 0, 0, None),
    ("SYN-GRP-FINREP-READ", "SYN-SYS-FINREP", "Finance Reports readers", "read", "medium", 0, 0, None),
    ("SYN-GRP-FINREP-WRITE", "SYN-SYS-FINREP", "Finance Reports editors", "write", "medium", 1, 0, None),
    ("SYN-GRP-DWH-READ", "SYN-SYS-DWH", "Data Warehouse readers", "read", "medium", 0, 0, None),
    ("SYN-GRP-DWH-WRITE", "SYN-SYS-DWH", "Data Warehouse writers", "write", "medium", 1, 0, None),
    ("SYN-GRP-PAYROLL-VIEW", "SYN-SYS-PAYROLL", "Payroll viewers", "read", "high", 1, 0, None),
    ("SYN-GRP-PAYROLL-ADMIN", "SYN-SYS-PAYROLL", "Payroll administrators", "admin", "high", 1, 0, None),
    ("SYN-GRP-AWSPROD-READ", "SYN-SYS-AWSPROD", "AWS production read-only", "read", "high", 1, 0, None),
    ("SYN-GRP-AWSPROD-ADMIN", "SYN-SYS-AWSPROD", "AWS production administrators", "admin", "high", 1, 0, None),
    ("SYN-GRP-LEGAL-HOLD", "SYN-SYS-LEGAL", "Legal hold: mailbox and files preserved", "read", "medium", 1, 1, None),
    ("SYN-GRP-CONTRACTOR-PORTAL", "SYN-SYS-PORTAL", "Contractor portal", "write", "low", 0, 0, 90),
]

EVERYONE = ["SYN-GRP-SLACK-MEMBER", "SYN-GRP-CONFLUENCE-READ", "SYN-GRP-JIRA-READ"]

# role -> (department, title, birthright groups beyond EVERYONE, groups allowed on request)
ROLES = {
    "software_engineer": ("Engineering", "Software Engineer", ["SYN-GRP-GITHUB-READ", "SYN-GRP-VPN-USER"],
                          ["SYN-GRP-JIRA-WRITE", "SYN-GRP-GITHUB-ENG", "SYN-GRP-CONFLUENCE-ENG", "SYN-GRP-DWH-READ"]),
    "engineering_manager": ("Engineering", "Engineering Manager", ["SYN-GRP-GITHUB-READ", "SYN-GRP-VPN-USER"],
                            ["SYN-GRP-JIRA-WRITE", "SYN-GRP-GITHUB-ENG", "SYN-GRP-CONFLUENCE-ENG", "SYN-GRP-DWH-READ"]),
    "data_analyst": ("Data", "Data Analyst", ["SYN-GRP-DWH-READ"],
                     ["SYN-GRP-JIRA-WRITE", "SYN-GRP-CONFLUENCE-FINANCE", "SYN-GRP-FINREP-READ",
                      "SYN-GRP-SALESFORCE-READ", "SYN-GRP-VPN-USER"]),
    "finance_analyst": ("Finance", "Finance Analyst", ["SYN-GRP-FINREP-READ", "SYN-GRP-CONFLUENCE-FINANCE"],
                        ["SYN-GRP-JIRA-WRITE", "SYN-GRP-DWH-READ", "SYN-GRP-VPN-USER"]),
    "finance_manager": ("Finance", "Finance Manager", ["SYN-GRP-FINREP-READ", "SYN-GRP-CONFLUENCE-FINANCE"],
                        ["SYN-GRP-JIRA-WRITE", "SYN-GRP-DWH-READ", "SYN-GRP-VPN-USER"]),
    "sales_rep": ("Sales", "Account Executive", ["SYN-GRP-SALESFORCE-READ", "SYN-GRP-SALESFORCE-EDIT"],
                  ["SYN-GRP-DWH-READ", "SYN-GRP-JIRA-WRITE"]),
    "sales_manager": ("Sales", "Sales Manager", ["SYN-GRP-SALESFORCE-READ", "SYN-GRP-SALESFORCE-EDIT"],
                      ["SYN-GRP-DWH-READ", "SYN-GRP-JIRA-WRITE"]),
    "marketing_specialist": ("Marketing", "Marketing Specialist", ["SYN-GRP-SALESFORCE-READ"],
                             ["SYN-GRP-JIRA-WRITE", "SYN-GRP-DWH-READ"]),
    "marketing_intern": ("Marketing", "Marketing Intern", [], ["SYN-GRP-JIRA-WRITE"]),
    "hr_partner": ("People", "HR Partner", [], ["SYN-GRP-JIRA-WRITE"]),
    "it_admin": ("IT", "IT Administrator", ["SYN-GRP-VPN-USER", "SYN-GRP-GITHUB-READ", "SYN-GRP-JIRA-WRITE"],
                 ["SYN-GRP-CONFLUENCE-ENG", "SYN-GRP-DWH-READ"]),
    "legal_counsel": ("Legal", "Legal Counsel", [], ["SYN-GRP-JIRA-WRITE"]),
    "executive": ("Executive", "Executive", ["SYN-GRP-VPN-USER"], ["SYN-GRP-FINREP-READ", "SYN-GRP-DWH-READ"]),
    "contractor_engineer": ("Engineering", "Contract Engineer", ["SYN-GRP-GITHUB-READ", "SYN-GRP-CONTRACTOR-PORTAL"],
                            ["SYN-GRP-JIRA-WRITE", "SYN-GRP-GITHUB-ENG"]),
    "contractor_analyst": ("Data", "Contract Analyst", ["SYN-GRP-DWH-READ", "SYN-GRP-CONTRACTOR-PORTAL"],
                           ["SYN-GRP-JIRA-WRITE"]),
}

# The named people: (id, name, role, manager, employment, status, start, end, extra groups held)
NAMED = [
    ("SYN-EMP-0001", "Margaret Ellison", "executive", None, "employee", "active", "2019-02-04", None, []),
    ("SYN-EMP-0002", "Tomas Herrera", "engineering_manager", "SYN-EMP-0001", "employee", "active", "2019-05-13", None, []),
    ("SYN-EMP-0003", "Ruth Adeyemi", "finance_manager", "SYN-EMP-0001", "employee", "active", "2019-08-19", None, []),
    ("SYN-EMP-0004", "Callum Baird", "sales_manager", "SYN-EMP-0001", "employee", "active", "2020-01-06", None, []),
    ("SYN-EMP-0005", "Ines Duarte", "marketing_specialist", "SYN-EMP-0001", "employee", "active", "2020-03-02", None, []),
    ("SYN-EMP-0006", "Kofi Asante", "hr_partner", "SYN-EMP-0001", "employee", "active", "2020-04-20", None, []),
    ("SYN-EMP-0007", "Arjun Mehta", "software_engineer", "SYN-EMP-0060", "employee", "active", "2022-06-13", None,
     ["SYN-GRP-GITHUB-ENG"]),
    ("SYN-EMP-0008", "Elena Petrova", "legal_counsel", "SYN-EMP-0001", "employee", "active", "2021-01-11", None, []),
    ("SYN-EMP-0009", "Sam Rivera", "it_admin", "SYN-EMP-0002", "employee", "active", "2021-09-06", None, []),
    ("SYN-EMP-0012", "Priya Nair", "finance_analyst", "SYN-EMP-0040", "employee", "active", "2021-11-01", None,
     ["SYN-GRP-JIRA-WRITE", "SYN-GRP-FINREP-WRITE", "SYN-GRP-PAYROLL-ADMIN"]),
    ("SYN-EMP-0021", "Maya Chen", "data_analyst", "SYN-EMP-0040", "employee", "active", "2023-02-27", None, []),
    ("SYN-EMP-0031", "Leo Martins", "data_analyst", "SYN-EMP-0040", "employee", "active", "2023-07-10", None, []),
    ("SYN-EMP-0040", "Grace Okafor", "finance_manager", "SYN-EMP-0003", "employee", "active", "2020-10-05", None,
     ["SYN-GRP-FINREP-WRITE"]),
    ("SYN-EMP-0052", "Dev Rao", "software_engineer", "SYN-EMP-0060", "employee", "active", "2026-10-19", None, []),
    ("SYN-EMP-0060", "Hannah Lee", "engineering_manager", "SYN-EMP-0002", "employee", "active", "2021-03-15", None,
     ["SYN-GRP-GITHUB-ENG", "SYN-GRP-JIRA-WRITE"]),
    ("SYN-EMP-0070", "Omar Haddad", "sales_rep", "SYN-EMP-0004", "employee", "active", "2024-01-08", None, []),
    ("SYN-EMP-0098", "Rosa Lindqvist", "marketing_specialist", "SYN-EMP-0005", "employee", "leaver", "2022-04-04",
     "2026-10-09", []),
    ("SYN-EMP-0120", "Felix Wagner", "marketing_intern", "SYN-EMP-0005", "employee", "active", "2026-09-07", None, []),
    ("SYN-EMP-0150", "Jonas Berg", "contractor_engineer", "SYN-EMP-0060", "contractor", "active", "2026-04-01",
     "2026-11-30", ["SYN-GRP-JIRA-WRITE", "SYN-GRP-GITHUB-ENG"]),
    ("SYN-EMP-0161", "Ana Souza", "contractor_engineer", "SYN-EMP-0060", "contractor", "active", "2026-05-04",
     "2026-10-15", ["SYN-GRP-JIRA-WRITE", "SYN-GRP-GITHUB-ENG"]),
    ("SYN-EMP-0162", "Victor Lam", "contractor_analyst", "SYN-EMP-0040", "contractor", "active", "2026-03-02",
     "2026-10-17", ["SYN-GRP-JIRA-WRITE", "SYN-GRP-LEGAL-HOLD"]),
    ("SYN-EMP-0163", "Mira Kaur", "contractor_engineer", "SYN-EMP-0060", "contractor", "active", "2026-06-01",
     "2026-10-19", ["SYN-GRP-JIRA-WRITE"]),
    ("SYN-EMP-0164", "Paul Okonkwo", "contractor_analyst", "SYN-EMP-0040", "contractor", "active", "2026-07-06",
     "2026-10-28", []),
    ("SYN-EMP-0215", "Nina Kowalski", "finance_analyst", "SYN-EMP-0040", "employee", "pending_start", "2026-10-26",
     None, []),
]

FIRST = ["Aisha", "Ben", "Carla", "Daniel", "Esther", "Farid", "Gemma", "Hugo", "Isla", "Jamal", "Keiko", "Liam",
         "Mateo", "Noor", "Oscar", "Paula", "Quentin", "Rhea", "Sofia", "Theo", "Uma", "Viktor", "Wendy", "Xavier",
         "Yara", "Zane", "Amir", "Bianca", "Chidi", "Dana", "Emil", "Fiona", "Gustav", "Hana", "Ivan", "Julia"]
LAST = ["Abbott", "Banerjee", "Castillo", "Dubois", "Eriksen", "Fischer", "Gallagher", "Hosseini", "Ito", "Jansen",
        "Kim", "Lopez", "Moreau", "Novak", "Osei", "Park", "Quinn", "Rossi", "Sato", "Tan", "Ueda", "Varga",
        "Walsh", "Yilmaz", "Zhou", "Andersen", "Brennan", "Costa", "Dalton", "Esposito"]

# Generated roles, roughly a company's mix; managers by department.
MIX = [("software_engineer", 75), ("data_analyst", 22), ("finance_analyst", 16), ("sales_rep", 48),
       ("marketing_specialist", 24), ("hr_partner", 8), ("it_admin", 6), ("legal_counsel", 4),
       ("contractor_engineer", 10), ("contractor_analyst", 4), ("engineering_manager", 6), ("sales_manager", 3)]
MANAGERS = {"Engineering": ["SYN-EMP-0060", "SYN-EMP-0002"], "Data": ["SYN-EMP-0040"], "Finance": ["SYN-EMP-0040",
            "SYN-EMP-0003"], "Sales": ["SYN-EMP-0004"], "Marketing": ["SYN-EMP-0005"], "People": ["SYN-EMP-0006"],
            "IT": ["SYN-EMP-0002"], "Legal": ["SYN-EMP-0008"], "Executive": ["SYN-EMP-0001"]}


def email(name: str, taken: set[str]) -> str:
    base = name.lower().replace(" ", ".")
    address, n = f"{base}@syn-corp.example", 2
    while address in taken:
        address, n = f"{base}{n}@syn-corp.example", n + 1
    taken.add(address)
    return address


def fake_secret(*parts: str) -> str:
    return "FAKE-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def people() -> list[dict]:
    """Every employee, named first, then the generated rest, in ID order."""
    rng = random.Random(SEED)
    taken: set[str] = set()
    out = []
    named_ids = {p[0] for p in NAMED}
    for pid, name, role, manager, employment, status, start, end, extra in NAMED:
        dept, title, _, _ = ROLES[role]
        out.append(dict(id=pid, name=name, email=email(name, taken), dept=dept, title=title, role=role,
                        manager_id=manager, employment=employment, status=status, start_date=start, end_date=end,
                        extra=extra))
    roles = [r for r, n in MIX for _ in range(n)]
    rng.shuffle(roles)
    free_ids = [f"SYN-EMP-{n:04d}" for n in range(1, COMPANY_SIZE + 1) if f"SYN-EMP-{n:04d}" not in named_ids]
    for pid, role in zip(free_ids, roles):
        dept, title, _, allowed = ROLES[role]
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        contractor = role.startswith("contractor")
        leaver = not contractor and rng.random() < 0.03
        start = f"20{rng.randint(19, 25)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        end = (f"2027-{rng.randint(1, 6):02d}-{rng.randint(1, 28):02d}" if contractor
               else (f"2026-{rng.randint(6, 9):02d}-{rng.randint(1, 28):02d}" if leaver else None))
        extra = rng.sample(allowed, k=min(len(allowed), rng.randint(0, 2)))
        out.append(dict(id=pid, name=name, email=email(name, taken), dept=dept, title=title, role=role,
                        manager_id=rng.choice(MANAGERS[dept]), employment="contractor" if contractor else "employee",
                        status="leaver" if leaver else "active", start_date=start, end_date=end, extra=extra))
    return sorted(out, key=lambda p: p["id"])


def load(conn) -> None:
    """Write the base data into an empty sandbox."""
    conn.executemany("INSERT INTO systems (id, name, owner_dept, sensitivity) VALUES (?, ?, ?, ?)", SYSTEMS)
    conn.executemany("INSERT INTO groups (id, system_id, name, level, sensitivity, needs_manager, protected, max_days) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", GROUPS)
    for role, (_, _, birthright, allowed) in ROLES.items():
        conn.executemany("INSERT INTO role_policy (role, group_id, kind) VALUES (?, ?, ?)",
                         [(role, g, "birthright") for g in EVERYONE + birthright]
                         + [(role, g, "allowed") for g in allowed])
    for p in people():
        conn.execute("INSERT INTO employees (id, name, email, dept, title, role, manager_id, employment, status, "
                     "start_date, end_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (p["id"], p["name"], p["email"], p["dept"], p["title"], p["role"], p["manager_id"],
                      p["employment"], p["status"], p["start_date"], p["end_date"]))
        if p["status"] == "pending_start":
            continue                                  # a new starter has nothing yet: onboarding grants it
        groups = EVERYONE + ROLES[p["role"]][2] + [g for g in p["extra"] if g not in EVERYONE]
        removed = p["end_date"] if p["status"] == "leaver" else None
        for g in dict.fromkeys(groups):
            conn.execute("INSERT INTO memberships (employee_id, group_id, granted_at, removed_at, actor, stamp) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (p["id"], g, f"{p['start_date']}T09:00:00Z",
                                                       removed and f"{removed}T17:00:00Z", SEEDED, SEEDED))
        if p["status"] == "active":
            conn.execute("INSERT INTO credentials (id, employee_id, system_id, kind, secret, created_at, stamp) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (f"SYN-CRED-{p['id'][-4:]}", p["id"], "SYN-SYS-SSO", "initial_password",
                          fake_secret(p["id"], "sso"), f"{p['start_date']}T09:00:00Z", SEEDED))
