from __future__ import annotations

import re

_DROP_TOKENS = {
    "team", "esports", "esport", "gaming", "e-sports", "e-sport",
    "club", "dota", "dota2", "ex", "former",
}

_ALIASES = {
    "navi": "natus vincere",
    "natus vincere": "natus vincere",
    "ngx": "nigma galaxy",
    "nigma": "nigma galaxy",
    "flc": "falcons",
    "team falcons": "falcons",
    "falcons": "falcons",
    "bb": "betboom",
    "bb4": "betboom",
    "betboom team": "betboom",
    "ts": "spirit",
    "ts8": "spirit",
    "team spirit": "spirit",
    "spirit": "spirit",
    "liquid": "liquid",
    "team liquid": "liquid",
    "vp": "virtus pro",
    "virtus pro": "virtus pro",
    "virtuspro": "virtus pro",
    "pari": "parivision",
    "pari vision": "parivision",
    "parivision": "parivision",
    # BLAST Slam / common Dota teams previously needing manual mapping because
    # Steam's GetLiveLeagueGames returns inconsistent labels for them.
    "heroic": "heroic",
    "heroic2": "heroic",
    "ex heroic": "heroic",
    "exheroic": "heroic",
    "ex-heroic": "heroic",
    "glyph": "glyph",
    "tundra": "tundra",
    "tundra esports": "tundra",
    "og": "og",
    "aurora": "aurora",
    "aurora1": "aurora",
    "aur1": "aurora",
    "xtreme": "xtreme",
    "xtreme gaming": "xtreme",
    "yandex": "yandex",
    "team yandex": "yandex",
    "rnx": "rekonix",
    "rekonix": "rekonix",
    "lgd": "lgd",
    "lgd gaming": "lgd",
    "psg lgd": "lgd",
    "psglgd": "lgd",
    "eg": "evil geniuses",
    "evil geniuses": "evil geniuses",
    "gl": "gamerlegion",
    "gamerlegion": "gamerlegion",
    "playtime": "playtime",
    "ivory": "glyph",
    "grind back": "grind",
    "team grind": "grind",
    "yangon galacticos": "yangon galacticos",
    "yangon": "yangon galacticos",
    "mentality monster": "mentality monsters",
    "mentality monsters": "mentality monsters",
    "mentality": "mentality monsters",
    # WEU / DreamLeague qualifiers 2026
    "1win": "1win",
    "1 win": "1win",
    "l1ga": "l1ga",
    "l1ga team": "l1ga",
    "liga team": "l1ga",
    "mouz": "mouz",
    "mousesports": "mouz",
    "power rangers": "power rangers",
    "nemiga": "nemiga",
    "nemiga gaming": "nemiga",
    "zero tenacity": "zero tenacity",
    "geek fam": "geek fam",
    "geekfam": "geek fam",
    "execration": "execration",
    "fnatic": "fnatic",
    "talon": "talon",
    "talon esports": "talon",
    "carstensz": "carstensz",
    "carstensz esports": "carstensz",
    "roar": "roar",
    "roar gaming": "roar",
    "bleed": "bleed",
    "bleed esports": "bleed",
}


def norm_team(value: str | None) -> str:
    """Normalize team names across Steam and Polymarket labels.

    This is deliberately conservative: it removes generic esports/team words,
    folds punctuation, and applies a small alias table for common Dota names.
    """
    text = (value or "").casefold().replace("&", " and ")
    text = text.replace(".", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _ALIASES.get(text, text)
    tokens = [tok for tok in text.split() if tok not in _DROP_TOKENS]
    normalized = " ".join(tokens) or text
    return _ALIASES.get(normalized, normalized)


def teams_match(a: str | None, b: str | None) -> bool:
    na = norm_team(a)
    nb = norm_team(b)
    if not na or not nb:
        return False
    return na == nb
