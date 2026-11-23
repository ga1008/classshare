"""Replay an independently sealed deletion register against a quiesced restore copy."""

import hashlib
import json
from pathlib import Path
import re

from . import minigame_packages as packages
from . import minigame_platform as platform


def register_snapshot(directory):
    directory = Path(directory).resolve(strict=True)
    digest = hashlib.sha256()
    records = []
    seen = set()
    for path in sorted(directory.iterdir()):
        if (
            path.is_symlink()
            or not path.is_file()
            or not re.fullmatch(r"[a-f0-9]{32}\.json", path.name)
            or path.stat().st_size > 2048
        ):
            raise ValueError("删除登记含不完整或未知条目，请封存完整登记后再恢复")
        r = json.loads(path.read_text(encoding="utf-8"))
        if (
            set(r) != {"gameId", "jobId", "actor", "createdAt"}
            or r["jobId"] != path.stem
            or not isinstance(r["gameId"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", r["gameId"])
            or r["gameId"] in seen
            or type(r["actor"]) is not int
            or r["actor"] < 1
            or type(r["createdAt"]) is not int
            or r["createdAt"] < 1
        ):
            raise ValueError("删除登记标识或关联无效")
        seen.add(r["gameId"])
        digest.update((packages.validation.canonical(r) + "\n").encode())
        records.append(r)
    return digest.hexdigest(), records


def verify_versions(excluded):
    """Prove that every surviving ready/retired runtime matches its database fingerprint."""
    with platform.game_db() as conn:
        rows = conn.execute(
            "SELECT game_id,id,digest,files_json FROM mg_versions WHERE status IN ('ready','retired')"
        ).fetchall()
        dangling = conn.execute("""SELECT g.id FROM mg_games g LEFT JOIN mg_versions v ON v.id=g.current_version_id
            AND v.game_id=g.id AND v.status='ready' WHERE g.status='listed' AND v.id IS NULL""").fetchall()
    if any(r[0] not in excluded for r in dangling):
        raise ValueError("恢复库的发布指针未指向对应已验收版本")
    count = 0
    for row in rows:
        if row["game_id"] in excluded:
            continue
        path = packages.asset_path("version", row["game_id"], row["id"])
        packages.marker(path, row["game_id"], row["id"])
        index = json.loads(packages.checked_dir(path, path / "validated.json").read_text(encoding="utf-8"))
        files = json.loads(row["files_json"])
        if (
            index["files"] != files
            or hashlib.sha256(packages.validation.canonical(files).encode()).hexdigest() != row["digest"]
        ):
            raise ValueError("数据库与恢复的版本清单不一致")
        for name, info in files.items():
            asset = packages.checked_dir(path, path / "runtime" / packages.validation.clean_member(name))
            if asset.stat().st_size != info["size"]:
                raise ValueError("恢复后的运行素材大小不一致")
            with asset.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != info["sha256"]:
                    raise ValueError("恢复后的运行素材摘要不一致")
        for name in ("thumbnail.png", "upload.zip"):
            if not packages.checked_dir(path, path / name).is_file():
                raise ValueError("恢复卷缺少原始包或缩略图")
        count += 1
    return count


def reconcile(directory, expected_digest, *, apply=False):
    digest, records = register_snapshot(directory)
    if digest != expected_digest:
        raise ValueError("删除登记与独立封存摘要不符；保持副本离线")
    excluded = {r["gameId"] for r in records}
    versions = verify_versions(excluded)
    with platform.game_db() as conn:
        unsealed = conn.execute("SELECT game_id,id FROM mg_admin_jobs WHERE kind='delete'").fetchall()
        known = {r["jobId"]: r["gameId"] for r in records}
        if any(known.get(r["id"]) != r["game_id"] for r in unsealed):
            raise ValueError("恢复数据库包含封存登记未覆盖的删除作业")
        leases = conn.execute("SELECT COUNT(*) FROM mg_launch_leases").fetchone()[0]
    affected = []
    for r in records:
        game = r["gameId"]
        with platform.game_db() as conn:
            row = conn.execute("SELECT status FROM mg_games WHERE id=?", (game,)).fetchone()
            assets = conn.execute(
                "SELECT kind,asset_id FROM mg_admin_assets WHERE game_id=?", (game,)
            ).fetchall()
            ids = {
                v[0] for v in conn.execute("SELECT id FROM mg_versions WHERE game_id=?", (game,)).fetchall()
            }
        ids.update(a["asset_id"] for a in assets if a["kind"] == "version")
        parent = packages.checked_dir(platform.game_root(), platform.game_root() / "versions" / game)
        if parent.exists() and any(p.name not in ids for p in parent.iterdir()):
            raise ValueError("版本卷包含数据库未登记的独占目录，请核对恢复点")
        stage_ids = {a["asset_id"] for a in assets if a["kind"] == "stage"}
        stage_root = packages.checked_dir(platform.game_root(), platform.game_root() / "staging")
        if stage_root.exists():
            for subdir in stage_root.iterdir():
                owner = packages.checked_dir(stage_root, subdir / ".minigame-owner")
                if (
                    owner.is_file()
                    and owner.read_text(encoding="ascii").startswith(game + "/")
                    and subdir.name not in stage_ids
                ):
                    raise ValueError("暂存卷包含恢复库未登记的文件包")
        if not row or row[0] == "deleted":
            if ids or stage_ids:
                raise ValueError("已删除游戏仍残留登记资产")
            continue
        affected.append(game)
        if not apply:
            continue
        with packages.game_lock(game), platform.asset_usage(game, exclusive=True):
            with platform.game_db() as conn:
                platform.db_start(conn, game)
                token = packages.uid()
                conn.execute(
                    """UPDATE mg_games SET status='deleting',policy_revision=policy_revision+1 WHERE id=?""",
                    (game,),
                )
                conn.execute("""UPDATE mg_launch_leases SET revoked=1 WHERE game_id=?""", (game,))
                conn.execute(
                    """INSERT INTO mg_admin_jobs(id,game_id,version_id,actor,kind,status,stage,token,created)
                    VALUES(?,?,'',?,'delete','running','恢复重放',?,?) ON CONFLICT(id) DO UPDATE
                    SET status='running',token=excluded.token,stage=excluded.stage""",
                    (r["jobId"], game, r["actor"], token, r["createdAt"]),
                )
                job = dict(packages.one(conn, "mg_admin_jobs", r["jobId"]))
                conn.commit()
            packages.delete_job(job)
    if apply:
        with platform.game_db() as conn:
            platform.db_start(conn)
            conn.execute("DELETE FROM mg_launch_leases")
            conn.execute("DELETE FROM mg_admin_preview_receipts")
            conn.commit()
        if register_snapshot(directory)[0] != expected_digest:
            raise ValueError("重放期间登记发生变更，请使用最新封存摘要重新核对")
    return {
        "mode": "applied" if apply else "dry-run",
        "registerDigest": digest,
        "records": len(records),
        "gamesToErase": len(affected),
        "verifiedSurvivingVersions": versions,
        "previousLeases": leases,
    }
