"""Your sandbox: your domain's data and state, where every effect of your system lands.

    sandbox/seed.py    reset() empties it; seed(task) loads your data and a task's inputs
    sandbox/queue.py   waiting(): the work the queue page shows

All data access lives here. Add a module per kind of record (sandbox/orders.py,
say) with the functions that read and write it: your product's pages call them,
and your agent's tools wrap the same functions, so a change made by hand and the
same change made by the agent run the same code. app/ imports nothing from agent/
but agent.service, so it never reaches the data through the agent's code.

Every record is synthetic and marked as such (a field, or an ID prefix such as
SYN-), and every secret is fake. Keep every file the sandbox writes under
harness.paths.state_dir(), and call state_dir() when you need it, never at import
time: run.py moves it, so experiments never touch the app's data.
"""
