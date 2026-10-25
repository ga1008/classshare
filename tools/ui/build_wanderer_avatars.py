"""Write the 散修 geometric avatars to static/img/wanderers/<slug>.svg (optional).

The avatars are rendered at runtime by ``classroom_app.services.wanderer_avatar_service``
(the avatar endpoint no longer needs these files); this script only exports them,
e.g. for design review.

Run:  python tools/ui/build_wanderer_avatars.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "static" / "img" / "wanderers"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from classroom_app.services.wanderer_avatar_service import render_wanderer_avatar_svg
    from classroom_app.services.wanderer_persona_seeds import WANDERER_SEEDS

    OUT.mkdir(parents=True, exist_ok=True)
    for seed in WANDERER_SEEDS:
        (OUT / f"{seed['slug']}.svg").write_text(render_wanderer_avatar_svg(seed["slug"]), encoding="utf-8", newline="
")
    print(f"wrote {len(WANDERER_SEEDS)} wanderer avatars to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
