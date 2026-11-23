"""Deterministic, platform-owned minigame engines. No package code is executed."""

import json

from fastapi import HTTPException


def fail(code, status=422):
    raise HTTPException(status, detail={"code": code})


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def matches_shape(value, spec, depth=0):
    if depth > 8:
        return False
    kind = spec["type"]
    if kind == "boolean":
        return type(value) is bool
    if kind == "enum":
        return type(value) is str and value in spec["values"]
    if kind == "integer":
        return integer(value, spec["minimum"], spec["maximum"])
    if kind == "string":
        return isinstance(value, str) and spec.get("minLength", 0) <= len(value) <= spec["maxLength"]
    if kind == "array":
        return (
            isinstance(value, list)
            and len(value) <= spec["maxItems"]
            and all(matches_shape(item, spec["items"], depth + 1) for item in value)
        )
    if kind == "object" and isinstance(value, dict):
        props = spec["properties"]
        return set(spec["required"]) <= value.keys() <= props.keys() and all(
            matches_shape(item, props[key], depth + 1) for key, item in value.items()
        )
    return False


def validate_private(value, fields):
    if not isinstance(value, dict) or value.keys() != fields.keys() or len(compact(value).encode()) > 32768:
        fail("INVALID_PRIVATE_SAVE")
    for key, spec in fields.items():
        v = value[key]
        kind = spec["type"]
        valid = (
            (kind == "boolean" and type(v) is bool)
            or (kind == "enum" and type(v) is str and v in spec["values"])
            or (kind == "integer" and integer(v, spec["min"], spec["max"]))
            or (kind == "document" and matches_shape(v, spec["schema"]))
        )
        if not valid:
            fail("INVALID_PRIVATE_SAVE")


def initial_state(game, rank):
    rules = game["rules"]
    if not rules:
        return {"turnSeat": 1, "rank": 0}
    if rules["engine"] == "grid-line-v1":
        return {"turnSeat": 1, "rank": 0, "board": [0] * (rules["size"] ** 2), "moves": 0}
    node = game["writeback"]["milestones"][rank]["resumeState"] or rules["initial"]
    return {"turnSeat": 1, "rank": rank, "node": node}


def apply_intent(game, state, seat, size, intent):
    if (
        not isinstance(intent, dict)
        or set(intent) != {"type", "payload"}
        or not isinstance(intent["payload"], dict)
    ):
        fail("INVALID_INTENT")
    rules = game["rules"]
    if not rules:
        fail("NOT_APPLICABLE")
    if rules["engine"] == "grid-line-v1":
        return play_grid(rules, state, seat, intent)
    for edge in rules["transitions"]:
        if (
            edge["from"] == state["node"]
            and edge["type"] == intent["type"]
            and compact(edge["payload"]) == compact(intent["payload"])
        ):
            if edge["actor"] == "turn" and seat != state["turnSeat"]:
                fail("WRONG_TURN", 409)
            node = edge["to"]
            state["node"] = node
            if edge["turn"] == "next":
                state["turnSeat"] = state["turnSeat"] % size + 1
            next_rank = state["rank"] + 1
            milestones = game["writeback"]["milestones"]
            if next_rank < len(milestones) and milestones[next_rank]["resumeState"] == node:
                state["rank"] = next_rank
            outcome = rules["states"][node]["outcome"]
            if outcome == "actorWins":
                state["winnerSeat"] = seat
            return outcome
    fail("ILLEGAL_MOVE")


def play_grid(rules, state, seat, intent):
    if state["turnSeat"] != seat:
        fail("WRONG_TURN", 409)
    size = rules["size"]
    payload = intent["payload"]
    if (
        intent["type"] != "place"
        or set(payload) != {"x", "y"}
        or not all(integer(v, 0, size - 1) for v in payload.values())
    ):
        fail("INVALID_INTENT")
    x, y = payload["x"], payload["y"]
    board = state["board"]
    if board[y * size + x]:
        fail("CELL_OCCUPIED", 409)
    board[y * size + x] = seat
    state["moves"] += 1
    for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
        length = 1
        for sign in (-1, 1):
            col, row = x + dx * sign, y + dy * sign
            while 0 <= col < size and 0 <= row < size and board[row * size + col] == seat:
                length += 1
                col, row = col + dx * sign, row + dy * sign
        if length >= rules["line"]:
            state["winnerSeat"] = seat
            return "actorWins"
    if state["moves"] == size**2:
        return "draw"
    state["turnSeat"] = 3 - seat
    return None
