"""No real secret is ever committed: make test fails if a file git tracks holds a
key-shaped string, or if a .env file is tracked. No tokens. Owner: Vipin Mourya.

The sandbox's own secrets start FAKE- and are generated at seed time into state/,
which git ignores, so they never match these patterns.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

from harness import ROOT

PATTERNS = {
    "an OpenAI-style key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "an AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "a GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    "a Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "a Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "a private key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "a password in a URL": re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@[^\s/]+", re.IGNORECASE),
}


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / p for p in out.decode().split("\0") if p]


@unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "needs git and the repository")
class NoSecretsTest(unittest.TestCase):
    def test_no_env_file_is_tracked(self):
        envs = [p.relative_to(ROOT) for p in tracked_files()
                if p.name == ".env" or (p.name.startswith(".env.") and p.name != ".env.example")]
        self.assertEqual(envs, [], f"a .env file is tracked: {envs}; remove it with git rm --cached and keep the key "
                                   "in your shell")

    def test_no_tracked_file_holds_a_key_shaped_string(self):
        found = []
        for path in tracked_files():
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue                                    # binary or unreadable: not a text secret
            for name, pattern in PATTERNS.items():
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    found.append(f"{path.relative_to(ROOT)}:{line}: looks like {name}")
        self.assertEqual(found, [], "a tracked file holds what looks like a real secret; remove it from the file and "
                                    "from history, and rotate the key:\n" + "\n".join(found))

    def test_the_patterns_catch_what_they_should_and_not_our_fake_secrets(self):
        hits = lambda s: [n for n, p in PATTERNS.items() if p.search(s)]
        # assembled here, so this file itself holds no key-shaped string
        fake_key, fake_url = "sk-" + "proj-" + "abcdEFGH1234ijklMNOP5678qrst", "https://alice" + ":hunter2@example.com/r"
        self.assertEqual(hits("OPENAI_API_KEY=" + fake_key), ["an OpenAI-style key"])
        self.assertEqual(hits(fake_url), ["a password in a URL"])
        self.assertEqual(hits("secret FAKE-3f9a1c2b7d4e5f60a1b2"), [])
        self.assertEqual(hits("git@github.com-vipin:mourya-vipin/agentic_ai_project.git"), [])
