"""Pure semantic state lookup, generated from the canonical CSS token aliases."""
import json
import logging
from pathlib import Path
from types import MappingProxyType

TONE_LEVELS = MappingProxyType({family: MappingProxyType(states) for family, states in
                              json.loads(Path(__file__).with_name("lq_tones.generated.json").read_text(encoding="utf-8")).items()})


def lq_tone(family, state, *, debug=False):
    known = isinstance(family, str) and isinstance(state, str) and family in TONE_LEVELS and state in TONE_LEVELS[family]
    if not known and debug:
        logging.getLogger(__name__).debug("Unknown LQ semantic state: %r %r", family, state)
    return {"name": f"{family}-{state}" if known else "neutral",
            "level": TONE_LEVELS[family][state] if known else "neutral", "known": bool(known)}
