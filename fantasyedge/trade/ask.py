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
    # No bare "back": "not enough back" is a complaint about the price, not a
    # request for a running back, and it read as one.
    "rb": "RB", "running back": "RB", "runningback": "RB", "rbs": "RB",
    "wr": "WR", "receiver": "WR", "wide receiver": "WR", "wideout": "WR",
    "wrs": "WR", "receivers": "WR",
    "te": "TE", "tight end": "TE", "tightend": "TE", "tight ends": "TE",
}

FEWER = ("too many", "too much", "fewer", "one for one", "1 for 1", "2 for 1",
         "giving up too", "gutting", "too many pieces", "simpler", "cleaner")
MORE_BACK = ("not enough", "more back", "lowball", "low ball", "shortchange",
             "not worth", "getting robbed", "bad for me", "too little")
UNREALISTIC = ("never accept", "wouldn't accept", "would not accept", "no way",
               "unrealistic", "too greedy", "insulting", "reject", "laugh")
KEEP = ("keep", "don't want to give", "dont want to give", "not giving",
        "won't give", "wont give", "off limits", "untouchable", "hands off")

# "I am already fine there" -- the opposite of a request, and the commonest
# thing said in the same breath as one.
SATISFIED = ("fine with", "happy with", "set at", "good at", "dont need",
             "do not need", "not looking for", "no more", "dont want",
             "do not want", "already have", "im good", "i am good", "sorted at",
             "stacked at", "deep at", "dont send", "do not send", "not another",
             "dont give me", "no thanks", "loaded at")

# Where one clause ends and the next begins. A sentence is split before its
# positions are read, because the two halves of "fine with my tight end, I want
# a receiver" mean opposite things and a whole-sentence match cannot tell.
#
# NOT on "get me" or "send me": those are want-markers, but "dont send me
# another quarterback" is a negation built out of one, and splitting there tore
# the "dont" off the position it belonged to.
CLAUSE = re.compile(r"[,;.]| but | and | though | however | i want | i need "
                    r"| looking for ")


@dataclass
class Ask:
    """Everything the search needs, and a plain record of what was read."""
    keep: list[str] = field(default_factory=list)       # my player ids
    want: list[str] = field(default_factory=list)       # positions to receive
    avoid: list[str] = field(default_factory=list)      # positions NOT to receive
    must_get: list[str] = field(default_factory=list)   # their player ids
    max_out: int = 2
    max_in: int = 2
    harder: bool = False      # they have to like it more
    richer: bool = False      # we have to gain more
    read: list[str] = field(default_factory=list)       # what we understood

    def as_dict(self) -> dict:
        return {"keep": self.keep, "want": self.want, "avoid": self.avoid,
                "must_get": self.must_get,
                "max_out": self.max_out, "max_in": self.max_in,
                "harder": self.harder, "richer": self.richer,
                "read": self.read}


def _key(name: str) -> str:
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\.?$", "", (name or "").lower().strip())
    return re.sub(r"[^a-z ]", "", n).strip()


def _names(name: str, roster: dict[str, str], text: str) -> bool:
    """Is this man named in the sentence, by full name or by surname?"""
    k = _key(name)
    if not k:
        return False
    if re.search(rf"\b{re.escape(k)}\b", text):
        return True
    last = k.split()[-1] if k.split() else ""
    if len(last) < 4:
        return False        # initials and short tags collide with real words
    shared = sum(1 for other in roster.values()
                 if _key(other).split() and _key(other).split()[-1] == last)
    return shared == 1 and bool(re.search(rf"\b{re.escape(last)}\b", text))


def _clauses(text: str) -> list[str]:
    """The sentence, cut where its meaning can change."""
    parts = [p.strip() for p in CLAUSE.split(text)]
    return [f" {p} " for p in parts if p]


def _positions_in(clause: str) -> list[str]:
    """Every position named in one clause, longest phrase first.

    Matched phrases are struck out as they are found, so "quarterback" cannot
    then match again as "back" and turn one quarterback into a running back
    nobody asked for.
    """
    out: list[str] = []
    left = clause
    for phrase in sorted(POSITIONS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", left):
            left = re.sub(rf"\b{re.escape(phrase)}\b", " ", left)
            pos = POSITIONS[phrase]
            if pos not in out:
                out.append(pos)
    return out


def parse(note: str, mine: dict[str, str], theirs: dict[str, str],
          base: Ask | None = None) -> Ask:
    """`mine`/`theirs` map player_id -> name for the two rosters on screen."""
    a = base or Ask()
    text = f" {_key(note)} "
    if not text.strip():
        return a

    # Players first, and by side: the same sentence means "protect him" about
    # one of mine and "go get him" about one of theirs. Full name or surname --
    # nobody types "Ashton Jeanty" when "Jeanty" is what they call him -- and
    # the surname only counts when exactly one man on that roster answers to
    # it, so a Williams on both ends is never guessed at.
    for side, roster, bucket, word in (("keep", mine, a.keep, "keep"),
                                       ("get", theirs, a.must_get, "target")):
        for pid, name in roster.items():
            if pid in bucket:
                continue
            if _names(name, roster, text):
                bucket.append(pid)
                a.read.append(f"{word} {name}")

    # THE IDIOMS COME OUT BEFORE THE POSITIONS GO IN. "Not enough back" is a
    # complaint about the price; left in the string it was also a request for a
    # running back, and the search went looking for one.
    hunt = text
    for phrase in FEWER + MORE_BACK + UNREALISTIC:
        hunt = hunt.replace(phrase, " ")

    # Positions, CLAUSE BY CLAUSE, because one sentence routinely says two
    # opposite things about two of them: "im fine with my tight end, i want a
    # solid wr" is a TE to leave alone and a receiver to go and get.
    #
    # The first version took the longest matching phrase in the whole string
    # and stopped. On that sentence "tightend" is longer than "wr", so it read
    # the position he was happy with, missed the one he asked for, and reported
    # the exact opposite of what he typed.
    for clause in _clauses(hunt):
        found = _positions_in(clause)
        if not found:
            continue
        if any(p in clause for p in SATISFIED):
            for pos in found:
                if pos not in a.avoid:
                    a.avoid.append(pos)
                    a.read.append(f"no {pos} needed")
                if pos in a.want:
                    a.want.remove(pos)
        else:
            for pos in found:
                if pos not in a.want and pos not in a.avoid:
                    a.want.append(pos)
                    a.read.append(f"you want a {pos}")

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
