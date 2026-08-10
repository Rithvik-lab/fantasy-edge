"""Turn "what's wrong with this trade?" into search constraints.

NO MODEL READS THIS SENTENCE. It is keyword matching against the two rosters
already on screen plus a short list of phrases, and it says out loud what it
understood so a miss is visible rather than silent. That is deliberate and it
is the same rule the rest of the app follows: every explanation is computed
from the numbers that produced the answer, so nothing can drift away from what
the engine actually did.

The chips in the interface produce these constraints directly. Typing is the
overflow for the case the chips do not cover, and it is allowed to understand
less than a person would -- what it must never do is understand something
different and then hide that it did.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

POSITIONS: dict[str, str] = {
    "qb": "QB", "quarterback": "QB", "quarter back": "QB",
    "rb": "RB", "running back": "RB", "runningback": "RB", "back": "RB",
    "wr": "WR", "receiver": "WR", "wide receiver": "WR", "wideout": "WR",
    "te": "TE", "tight end": "TE", "tightend": "TE",
}

FEWER = ("too many", "too much", "fewer", "one for one", "1 for 1", "2 for 1",
         "giving up too", "gutting", "too many pieces", "simpler", "cleaner")
MORE_BACK = ("not enough", "more back", "lowball", "low ball", "shortchange",
             "not worth", "getting robbed", "bad for me", "too little")
UNREALISTIC = ("never accept", "wouldn't accept", "would not accept", "no way",
               "unrealistic", "too greedy", "insulting", "reject", "laugh")
KEEP = ("keep", "don't want to give", "dont want to give", "not giving",
        "won't give", "wont give", "off limits", "untouchable", "hands off")


@dataclass
class Ask:
    """Everything the search needs, and a plain record of what was read."""
    keep: list[str] = field(default_factory=list)       # my player ids
    want: list[str] = field(default_factory=list)       # positions to receive
    must_get: list[str] = field(default_factory=list)   # their player ids
    max_out: int = 2
    max_in: int = 2
    harder: bool = False      # they have to like it more
    richer: bool = False      # we have to gain more
    read: list[str] = field(default_factory=list)       # what we understood

    def as_dict(self) -> dict:
        return {"keep": self.keep, "want": self.want, "must_get": self.must_get,
                "max_out": self.max_out, "max_in": self.max_in,
                "harder": self.harder, "richer": self.richer,
                "read": self.read}


def _key(name: str) -> str:
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\.?$", "", (name or "").lower().strip())
    return re.sub(r"[^a-z ]", "", n).strip()


def parse(note: str, mine: dict[str, str], theirs: dict[str, str],
          base: Ask | None = None) -> Ask:
    """`mine`/`theirs` map player_id -> name for the two rosters on screen."""
    a = base or Ask()
    text = f" {_key(note)} "
    if not text.strip():
        return a

    # Players first, and by side: the same sentence means "protect him" about
    # one of mine and "go get him" about one of theirs.
    for pid, name in mine.items():
        k = _key(name)
        if k and k in text and pid not in a.keep:
            a.keep.append(pid)
            a.read.append(f"keep {name}")
    for pid, name in theirs.items():
        k = _key(name)
        if k and k in text and pid not in a.must_get:
            a.must_get.append(pid)
            a.read.append(f"target {name}")

    # Positions, longest phrase first so "wide receiver" never matches as
    # "receiver" plus a stray word.
    for phrase in sorted(POSITIONS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            pos = POSITIONS[phrase]
            if pos not in a.want:
                a.want.append(pos)
                a.read.append(f"you want a {pos}")
            break

    if any(p in text for p in FEWER):
        a.max_out = 1
        a.read.append("send fewer players")
    if any(p in text for p in MORE_BACK):
        a.richer = True
        a.read.append("get more back")
    if any(p in text for p in UNREALISTIC):
        a.harder = True
        a.read.append("make it easier for them to say yes")
    if any(p in text for p in KEEP) and not a.keep:
        a.read.append("(name the player you want to keep and it will hold him)")

    return a
