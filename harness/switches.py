"""switches.yaml: a switch for each part of your design a pass may turn on or off,
loaded at start-up.

A switch is an entry with a `setting`, and usually the `values` it may take:

    critique:
      setting: "off"
      values: ["off", "external"]
      about: ...

An entry without `setting` is a group of switches, one per place the decision
applies to, each named with a dot:

    plan:
      refunds: {setting: "on", values: ["off", "on"]}           # plan.refunds
      status_questions: {setting: "off", values: ["off", "on"]}  # plan.status_questions

A switch never has switches under it: an entry with a `setting` and switches under
it is an error, and so is `added:` on a group. To split a flat switch, such as the
starter's `control`, into one per use case, replace it with a group of the same
name. A plain `name: value` is a switch with no list of values.
YAML reads a bare off and on as false and true; this loader turns them back into
"off" and "on".

A switch added after some runs were recorded says so with `added:` and the day
you add it (YYYY-MM-DD), on the switch itself: make check then expects it only in
run records started after that day.

run.py overrides switches for one arm with --set: --set plan.refunds=on sets
one, --set control.refunds=agent plan.refunds=on sets several. The app and run.py
both load the file once, at start-up; your agent reads the settings with
harness.runtime.switch(name).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from harness import ROOT


class SwitchError(ValueError):
    pass


@dataclass(frozen=True)
class Switch:
    name: str
    setting: object
    values: tuple = ()
    about: str = ""
    added: str | None = None
    extra: dict = field(default_factory=dict)


def _norm(value):
    if value is True:
        return "on"
    if value is False:
        return "off"
    return value


FIELDS = ("setting", "values", "about", "added")     # what a switch may say about itself


def _walk(prefix: str, data: dict, out: dict) -> None:
    for key, entry in (data or {}).items():
        name = f"{prefix}{key}"
        if not isinstance(entry, dict):
            out[name] = Switch(name, _norm(entry))
        elif "setting" in entry:
            if any(isinstance(v, dict) for k, v in entry.items() if k not in FIELDS):
                raise SwitchError(f"switches.yaml: {name} has a setting and switches under it; make it a group, "
                                  "without setting, values or about, or a single switch")
            values = tuple(_norm(v) for v in entry.get("values") or ())
            setting = _norm(entry["setting"])
            if values and setting not in values:
                raise SwitchError(f"switches.yaml: {name} is set to {setting!r}, which is not one of {list(values)}")
            added = entry.get("added")
            out[name] = Switch(name, setting, values, str(entry.get("about") or "").strip(),
                               str(added) if added else None,
                               {k: v for k, v in entry.items() if k not in FIELDS})
        elif "added" in entry:
            raise SwitchError(f"switches.yaml: added: belongs on a switch, not on the group {name}; "
                              f"put it on each switch under {name} that you added")
        elif any(k in entry for k in ("about", "values")):
            field = next(k for k in ("about", "values") if k in entry)
            raise SwitchError(f"switches.yaml: {field}: belongs on a switch, not on the group {name}; "
                              f"put it on each switch under {name}")
        elif entry:
            _walk(f"{name}.", entry, out)
        else:
            raise SwitchError(f"switches.yaml: {name} is empty; give it a setting")


def load(path: Path | None = None) -> dict[str, Switch]:
    """Every switch in switches.yaml, by its full name."""
    path = Path(path or ROOT / "switches.yaml")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise SwitchError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SwitchError(f"{path.name} must be a mapping of switch names")
    out: dict[str, Switch] = {}
    _walk("", data, out)
    return out


def settings(switches: dict[str, Switch]) -> dict:
    """Each switch's setting, by its full name: what every run record carries."""
    return {name: s.setting for name, s in switches.items()}


def _parse(switch: Switch, text: str):
    if switch.values:
        for value in switch.values:
            if str(value) == text:
                return value
        raise SwitchError(f"--set {switch.name}={text}: not one of {[str(v) for v in switch.values]}")
    if isinstance(switch.setting, bool):
        return text
    for kind in (int, float):
        if isinstance(switch.setting, kind):
            try:
                return kind(text)
            except ValueError as exc:
                raise SwitchError(f"--set {switch.name}={text}: not a number") from exc
    return text


def apply(switches: dict[str, Switch], overrides: list[str] | None) -> dict:
    """The settings with run.py's --set overrides applied (--set a=x b=y sets several)."""
    current = settings(switches)
    seen: set[str] = set()
    for item in overrides or []:
        if "=" not in item:
            raise SwitchError(f"--set {item}: write it as name=value")
        name, text = (part.strip() for part in item.split("=", 1))
        if name in seen:
            raise SwitchError(f"--set {name}: given twice; set each switch once")
        seen.add(name)
        if name not in switches:
            raise SwitchError(f"--set {name}: switches.yaml has no switch of that name "
                              f"(it has {', '.join(switches) or 'none'})")
        current[name] = _parse(switches[name], text)
    return current
