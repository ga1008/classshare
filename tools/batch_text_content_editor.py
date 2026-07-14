"""Interactive, dependency-free batch editor for text files.

Run from a terminal with::

    python tools\batch_text_content_editor.py

The script deliberately does not create backups: its final confirmation makes
the requested delete/replace operation directly.  Files are written to a
temporary file in the same directory and atomically replaced only after a
complete successful write, so a failed file remains untouched.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


SAMPLE_BYTES = 64 * 1024
TEXT_CHUNK_CHARS = 1024 * 1024
REGEX_MEMORY_WARNING_BYTES = 128 * 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 0.15
NO_SUFFIX = ""


@dataclass(frozen=True)
class EditConfig:
    """An already validated operation shared by the file workers."""

    is_regex: bool
    pattern_text: str
    replacement: str
    compiled_pattern: re.Pattern[str] | None = None


@dataclass(frozen=True)
class FileResult:
    path: Path
    status: str
    matches: int = 0
    message: str = ""


def suffix_label(suffix: str) -> str:
    return suffix if suffix else "（无扩展名）"


def format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def compact_error(exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def detect_text_encoding(path: Path) -> str | None:
    """Return a safe text encoding for a file sample, or ``None`` for binary.

    UTF-8 and GB18030 cover the usual project text files on Windows.  BOM based
    UTF-16/UTF-32 is handled before the NUL-byte binary check so those documents
    are not incorrectly discarded.
    """

    try:
        with path.open("rb") as handle:
            sample = handle.read(SAMPLE_BYTES)
    except OSError:
        return None

    if sample.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "utf-32"
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if not sample:
        return "utf-8"

    # NUL bytes and a high density of non-whitespace control characters are
    # reliable binary indicators for UTF-8/GB18030 documents.
    if b"\x00" in sample:
        return None
    controls = sum(
        byte < 32 and byte not in {8, 9, 10, 12, 13, 27} for byte in sample
    )
    if controls / len(sample) > 0.02:
        return None

    for encoding in ("utf-8", "gb18030"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return None


def iter_files(root: Path) -> Iterator[Path]:
    """Recursively yield files without following directory symlinks."""

    def ignore_walk_error(_: OSError) -> None:
        # Individual inaccessible files/dirs are reported when relevant.  A
        # scan should not stop because one directory is unavailable.
        return None

    for directory, _, names in os.walk(root, followlinks=False, onerror=ignore_walk_error):
        directory_path = Path(directory)
        for name in names:
            yield directory_path / name


def scan_supported_suffixes(root: Path) -> tuple[Counter[str], int]:
    """Inspect every file's leading bytes and count text-like suffixes."""

    suffixes: Counter[str] = Counter()
    unreadable = 0
    for path in iter_files(root):
        if path.is_symlink():
            continue
        encoding = detect_text_encoding(path)
        if encoding is None:
            # This also covers an unreadable file; it is deliberately omitted
            # from the selectable text suffix inventory.
            continue
        try:
            if path.is_file():
                suffixes[path.suffix.lower()] += 1
        except OSError:
            unreadable += 1
    return suffixes, unreadable


def print_suffix_inventory(suffixes: Counter[str]) -> list[str]:
    ordered = sorted(suffixes, key=lambda item: (item == NO_SUFFIX, item))
    print("\n检测到以下文本类文件后缀（括号内为抽样判定为文本的文件数）：")
    for index, suffix in enumerate(ordered, start=1):
        print(f"  {index:>3}. {suffix_label(suffix):<20} {suffixes[suffix]}")
    print("输入序号（可用逗号或空格分隔），输入 all 选择全部。")
    return ordered


def prompt_directory() -> Path | None:
    """Use the native folder picker when possible, then fall back to typing."""

    print("\n请选择目标文件夹。关闭选择框后可在终端手动输入路径。")
    selected = ""
    try:
        import tkinter as tk
        from tkinter import filedialog

        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="选择需要批量处理的文件夹", mustexist=True)
        window.destroy()
    except Exception:
        # A remote/terminal-only Python environment can lack a GUI.  The
        # terminal prompt below keeps the script usable in that situation.
        selected = ""

    while True:
        raw = selected or input("目标文件夹路径（直接回车退出）：").strip().strip('"')
        selected = ""
        if not raw:
            return None
        try:
            path = Path(raw).expanduser().resolve()
        except OSError as exc:
            print(f"路径无效：{compact_error(exc)}")
            continue
        if not path.is_dir():
            print("该路径不是可访问的文件夹，请重新输入。")
            continue
        return path


def prompt_suffix_selection(ordered: list[str]) -> set[str]:
    while True:
        raw = input("后缀选择：").strip().lower()
        if raw == "all":
            return set(ordered)
        tokens = [item for item in re.split(r"[,\s]+", raw) if item]
        if not tokens:
            print("请至少选择一个后缀。")
            continue
        selected: set[str] = set()
        valid = True
        for token in tokens:
            try:
                index = int(token)
            except ValueError:
                valid = False
                break
            if not 1 <= index <= len(ordered):
                valid = False
                break
            selected.add(ordered[index - 1])
        if valid:
            return selected
        print("选择无效，请输入上方显示的序号，例如：1, 3, 5。")


def prompt_yes_no(message: str) -> bool:
    while True:
        answer = input(f"{message} [y/N]：").strip().lower()
        if answer in {"y", "yes", "是", "确认", "1"}:
            return True
        if answer in {"", "n", "no", "否", "0"}:
            return False
        print("请输入 y 或 n。")


def prompt_multiline(message: str, *, allow_empty: bool) -> str:
    print(message)
    print("逐行输入；单独输入 <<END>> 结束。")
    while True:
        lines: list[str] = []
        try:
            while True:
                line = input()
                if line == "<<END>>":
                    break
                lines.append(line)
        except EOFError:
            raise KeyboardInterrupt from None
        value = "\n".join(lines)
        if value or allow_empty:
            return value
        print("匹配内容不能为空，请重新输入。")


def prompt_config() -> EditConfig | None:
    while True:
        mode = input("\n匹配模式：1. 正则表达式  2. 普通精确匹配：").strip()
        if mode in {"1", "2"}:
            break
        print("请输入 1 或 2。")
    is_regex = mode == "1"
    pattern = prompt_multiline(
        "请输入正则表达式（可使用 Python re 语法，如 (?m)^旧内容$）："
        if is_regex
        else "请输入要精确匹配的原文（按字面量匹配，不会把 .、* 等视作特殊字符）：",
        allow_empty=False,
    )
    compiled: re.Pattern[str] | None = None
    if is_regex:
        while True:
            try:
                compiled = re.compile(pattern)
                break
            except re.error as exc:
                print(f"正则表达式无效：{exc}")
                pattern = prompt_multiline("请重新输入正则表达式：", allow_empty=False)

    while True:
        action = input("匹配后操作：1. 删除匹配内容  2. 替换匹配内容：").strip()
        if action in {"1", "2"}:
            break
        print("请输入 1 或 2。")
    if action == "1":
        return EditConfig(is_regex, pattern, "", compiled)

    while True:
        replacement = prompt_multiline(
            "请输入替换内容（允许为空；正则模式可使用 \\1 或 \\g<name> 引用分组）：",
            allow_empty=True,
        )
        if compiled is not None:
            try:
                # Validate the replacement template before any worker writes a
                # temporary file.  It is valid even when the subject is empty.
                compiled.sub(replacement, "")
            except re.error as exc:
                print(f"正则替换内容无效：{exc}")
                continue
        return EditConfig(is_regex, pattern, replacement, compiled)


def create_temporary_path(path: Path) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{path.name}.batch-edit-",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary_path = Path(handle.name)
    handle.close()
    return temporary_path


def replace_plain_stream(
    source: object, destination: object, needle: str, replacement: str
) -> int:
    """Replace an exact string without loading a large file into memory.

    The tail starts at the first possible cross-chunk match, rather than at a
    fixed number of characters.  Therefore a match split across two chunks is
    edited exactly once and ordinary ``str.replace`` non-overlap semantics are
    preserved.
    """

    matches = 0
    tail = ""
    needle_length = len(needle)

    while True:
        chunk = source.read(TEXT_CHUNK_CHARS)  # type: ignore[attr-defined]
        if not chunk:
            break
        data = tail + chunk
        safe_end = max(0, len(data) - needle_length + 1)
        cursor = 0
        hold_from = safe_end

        while True:
            found_at = data.find(needle, cursor)
            if found_at < 0:
                break
            found_end = found_at + needle_length
            if found_end > safe_end:
                hold_from = found_at
                break
            destination.write(data[cursor:found_at])  # type: ignore[attr-defined]
            destination.write(replacement)  # type: ignore[attr-defined]
            matches += 1
            cursor = found_end

        destination.write(data[cursor:hold_from])  # type: ignore[attr-defined]
        tail = data[hold_from:]

    matches += tail.count(needle)
    destination.write(tail.replace(needle, replacement))  # type: ignore[attr-defined]
    return matches


def replace_regex_full(
    source: object, destination: object, config: EditConfig
) -> int:
    """Apply full-document Unicode regex semantics.

    General regular expressions can have unbounded look-around or repetitions,
    so correct full-document semantics cannot safely be split on arbitrary
    chunks using only Python's standard ``re`` module.  Exact matching uses the
    streaming implementation above; regex mode intentionally reads one file at
    a time, and worker concurrency is reduced to cap memory pressure.
    """

    content = source.read()  # type: ignore[attr-defined]
    assert config.compiled_pattern is not None
    updated, matches = config.compiled_pattern.subn(config.replacement, content)
    if matches:
        destination.write(updated)  # type: ignore[attr-defined]
    return matches


def process_file(path: Path, config: EditConfig) -> FileResult:
    """Edit one file, keeping its original intact unless replacement succeeds."""

    temporary_path: Path | None = None
    try:
        if path.is_symlink():
            return FileResult(path, "skipped_link", message="为避免替换符号链接，已跳过")
        encoding = detect_text_encoding(path)
        if encoding is None:
            return FileResult(path, "skipped_binary", message="不是可识别的文本文件")
        original_mode = stat.S_IMODE(path.stat().st_mode)
        temporary_path = create_temporary_path(path)
        with path.open("r", encoding=encoding, errors="strict", newline="") as source:
            with temporary_path.open("w", encoding=encoding, errors="strict", newline="") as destination:
                if config.is_regex:
                    matches = replace_regex_full(source, destination, config)
                else:
                    matches = replace_plain_stream(
                        source, destination, config.pattern_text, config.replacement
                    )
        if not matches:
            temporary_path.unlink(missing_ok=True)
            return FileResult(path, "unchanged")

        # Retain permissions while changing contents.  ``os.replace`` is
        # atomic within the target directory, unlike writing the source file
        # directly.
        os.chmod(temporary_path, original_mode)
        os.replace(temporary_path, path)
        return FileResult(path, "changed", matches=matches)
    except (OSError, UnicodeError, MemoryError, re.error) as exc:
        return FileResult(path, "failed", message=compact_error(exc))
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def collect_target_files(root: Path, selected_suffixes: set[str]) -> list[Path]:
    files: list[Path] = []
    for path in iter_files(root):
        try:
            if path.suffix.lower() in selected_suffixes:
                files.append(path)
        except OSError:
            # The worker will not be able to process this transiently missing
            # path either, so omit it rather than failing the full job.
            continue
    return files


def print_progress(completed: int, total: int, counts: Counter[str], *, final: bool) -> None:
    line = (
        f"\r进度 {completed}/{total} | 已修改 {counts['changed']} 个文件"
        f" | 匹配 {counts['matches']} 处 | 跳过 {counts['skipped_binary'] + counts['skipped_link']}"
        f" | 失败 {counts['failed']}"
    )
    print(line, end="\n" if final else "", flush=True)


def run_batch(root: Path, selected_suffixes: set[str], config: EditConfig) -> None:
    print("\n正在递归枚举所选后缀的文件…")
    files = collect_target_files(root, selected_suffixes)
    if not files:
        print("未找到所选后缀的文件，未进行任何修改。")
        return

    total_size = 0
    for path in files:
        try:
            total_size += path.stat().st_size
        except OSError:
            pass
    if config.is_regex:
        sizes: list[int] = []
        for path in files:
            try:
                sizes.append(path.stat().st_size)
            except OSError:
                continue
        largest_size = max(sizes, default=0)
        if largest_size >= REGEX_MEMORY_WARNING_BYTES:
            print(
                "提示：正则模式按完整文件进行 Unicode 匹配；最大文件为 "
                f"{format_size(largest_size)}，将以较低并发逐个/少量处理以控制内存。"
            )

    cpu_count = os.cpu_count() or 2
    workers = min(2, cpu_count) if config.is_regex else min(8, max(2, cpu_count))
    print(
        f"开始处理 {len(files)} 个目标后缀文件（合计 {format_size(total_size)}，"
        f"{workers} 个工作线程）…"
    )
    counts: Counter[str] = Counter()
    failures: list[FileResult] = []
    completed = 0
    last_progress_at = 0.0

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="batch-text-edit") as executor:
        futures = {executor.submit(process_file, path, config): path for path in files}
        for future in as_completed(futures):
            path = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # Defensive: one worker must not stop progress.
                result = FileResult(path, "failed", message=compact_error(exc))
            completed += 1
            counts[result.status] += 1
            counts["matches"] += result.matches
            if result.status == "failed":
                failures.append(result)
            now = time.monotonic()
            if (
                completed != len(files)
                and now - last_progress_at >= PROGRESS_INTERVAL_SECONDS
            ):
                print_progress(completed, len(files), counts, final=False)
                last_progress_at = now

    print_progress(completed, len(files), counts, final=True)
    print("\n处理完成：")
    print(f"  目标后缀文件：{len(files)}")
    print(f"  已修改文件：{counts['changed']}")
    print(f"  命中并处理：{counts['matches']} 处")
    print(f"  未命中文件：{counts['unchanged']}")
    print(f"  跳过二进制/无法识别文本：{counts['skipped_binary']}")
    print(f"  跳过符号链接：{counts['skipped_link']}")
    print(f"  处理失败：{counts['failed']}")
    if failures:
        print("\n失败文件（最多显示 10 个）：")
        for result in failures[:10]:
            print(f"  - {result.path}: {result.message}")
        if len(failures) > 10:
            print(f"  另有 {len(failures) - 10} 个失败文件未展开。")


def wait_for_exit() -> None:
    print("\n按任意键退出…", end="", flush=True)
    if os.name == "nt":
        try:
            import msvcrt

            msvcrt.getch()
            print()
            return
        except Exception:
            pass
    try:
        input()
    except EOFError:
        pass


def main() -> None:
    print("=" * 64)
    print("文本类文档批量内容删除 / 替换工具（仅使用 Python 标准库）")
    print("=" * 64)
    try:
        while True:
            root = prompt_directory()
            if root is None:
                print("已取消，未进行任何修改。")
                return
            print("\n正在扫描文件夹并识别文本类文件后缀…")
            suffixes, unreadable = scan_supported_suffixes(root)
            if not suffixes:
                print("未检测到可处理的文本类文件后缀，未进行任何修改。")
                return
            ordered = print_suffix_inventory(suffixes)
            selected_suffixes = prompt_suffix_selection(ordered)
            selection_text = "、".join(suffix_label(item) for item in sorted(selected_suffixes))
            print(f"\n目标文件夹：{root}")
            print(f"所选后缀：{selection_text}")
            if unreadable:
                print(f"扫描时有 {unreadable} 个文件无法读取，未列入后缀统计。")
            if prompt_yes_no("文件夹和后缀选择是否正确"):
                break
            print("将重新选择文件夹和后缀。")

        config = prompt_config()
        assert config is not None
        mode_label = "正则表达式" if config.is_regex else "普通精确匹配"
        action_label = "删除匹配内容" if not config.replacement else "替换匹配内容"
        print("\n即将执行以下操作（不会自动创建备份）：")
        print(f"  模式：{mode_label}")
        print(f"  操作：{action_label}")
        print(f"  匹配内容：{config.pattern_text!r}")
        if config.replacement:
            print(f"  替换内容：{config.replacement!r}")
        print("  写入方式：同目录临时文件完成后原子替换；单文件失败不会覆盖原文件。")
        if not prompt_yes_no("确认开始批量处理"):
            print("已取消，未进行任何修改。")
            return
        run_batch(root, selected_suffixes, config)
    except KeyboardInterrupt:
        print("\n已取消，当前未完成的文件会保留原文件；已完成替换的文件不会自动回滚。")
    finally:
        wait_for_exit()


if __name__ == "__main__":
    main()
