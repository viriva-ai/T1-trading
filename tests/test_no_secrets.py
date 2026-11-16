"""Repository hygiene: no credentials or tokens in tracked text files."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = [re.compile(p) for p in (r"request_token=[A-Za-z0-9]{20,}", r"\"access_token\"\s*:\s*\"[A-Za-z0-9]{16,}",
                                     r"KITE_API_(KEY|SECRET)\s*=\s*(?!your_)[a-z0-9]{12,}")]


def test_no_secret_like_strings():
    bad = []
    for p in ROOT.rglob("*"):
        if p.is_dir() or ".git" in p.parts or p.suffix in (".gz", ".pyc") or "__pycache__" in p.parts:
            continue
        text = p.read_text(errors="ignore")
        bad += [f"{p.relative_to(ROOT)}: {rx.pattern}" for rx in PATTERNS if rx.search(text)]
    assert not bad, bad
