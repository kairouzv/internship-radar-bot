"""Utilitaires texte : normalisation (sans accents, minuscules) et HTML -> texte."""
from __future__ import annotations

import html
import re
import unicodedata

from bs4 import BeautifulSoup

_QUOTES = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        " ": " ",
        " ": " ",
        " ": " ",
    }
)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str | None) -> str:
    """Minuscules, sans accents, espaces compactés (les retours ligne deviennent des espaces)."""
    s = html.unescape(s or "")
    s = strip_accents(s).lower().translate(_QUOTES)
    return re.sub(r"\s+", " ", s).strip()


def norm_lines(s: str | None) -> str:
    """Comme norm() mais conserve les retours ligne (frontières de phrases)."""
    s = html.unescape(s or "")
    s = strip_accents(s).lower().translate(_QUOTES)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*", "\n", s)
    return s.strip()


def html_to_text(raw: str | None) -> str:
    """Convertit du HTML (ou du texte) en texte avec retours ligne."""
    if not raw:
        return ""
    if "<" not in raw and "&" not in raw:
        return raw.strip()
    soup = BeautifulSoup(raw, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for p in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "tr"]):
        p.append("\n")
    text = soup.get_text()
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


_SUFFIXES = re.compile(
    r"\b(sa|sas|sarl|se|ag|plc|ltd|limited|inc|llc|llp|lp|gmbh|group|groupe|holding|holdings|co|company|partners|"
    r"international|france|paris|geneva|geneve|suisse|switzerland|corporate|and|et)\b"
)


def norm_company(s: str | None) -> str:
    s = norm(s)
    s = s.replace("&", " and ")
    s = _SUFFIXES.sub(" ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


_TITLE_NOISE = re.compile(
    r"\(\s*[hfmwdx]\s*[/\-]\s*[hfmwdx](?:\s*[/\-]\s*[hfmwdx])?\s*\)|\b[hf]/[hf]\b|\bm/f/d\b|\bm/w/d\b|\bf/m/d\b"
)


def norm_title(s: str | None) -> str:
    s = norm(s)
    s = _TITLE_NOISE.sub(" ", s)
    s = re.sub(r"[^a-z0-9&]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()
