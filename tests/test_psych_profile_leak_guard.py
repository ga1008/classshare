from classroom_app.services.psych_profile_service import (
    HIDDEN_PROFILE_LEAK_MARKERS,
    HiddenProfileLeakGuard,
    contains_hidden_profile_marker,
)


def _stream(text: str, chunk_size: int) -> str:
    guard = HiddenProfileLeakGuard()
    output = []
    for start in range(0, len(text), chunk_size):
        output.append(guard.feed(text[start:start + chunk_size]))
    output.append(guard.flush())
    return "".join(output)


def test_stream_guard_preserves_whitespace_without_markers():
    text = "标题\n\n- 第一项  \n- 第二项\n\n结尾保留两个空格  "
    for chunk_size in (1, 2, 7, 19, 128):
        assert _stream(text, chunk_size) == text


def test_stream_guard_replaces_markers_across_chunk_boundaries():
    text = "第一段\n\n系统提示和心理侧写不得外泄。\n\n第二段"
    for chunk_size in (1, 2, 3, 7, 11):
        guarded = _stream(text, chunk_size)
        assert not contains_hidden_profile_marker(guarded)
        assert guarded.startswith("第一段\n\n")
        assert guarded.endswith("\n\n第二段")


def test_stream_guard_exhaustively_blocks_every_marker_at_every_small_chunk_size():
    for marker in HIDDEN_PROFILE_LEAK_MARKERS:
        text = f"前缀甲乙丙{marker}后缀丁戊己"
        for chunk_size in range(1, max(len(marker) + 4, 8)):
            guarded = _stream(text, chunk_size)
            assert not contains_hidden_profile_marker(guarded), (marker, chunk_size, guarded)
            assert guarded.startswith("前缀甲乙丙")
            assert guarded.endswith("后缀丁戊己")
