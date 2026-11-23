import io
import json
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException

from classroom_app import dependencies
from classroom_app.services import minigame_packages as packages
from classroom_app.services import minigame_platform as platform
from classroom_app.services import minigame_package_recovery as recovery
from classroom_app.services import minigame_package_validation as validation
from test_minigame_platform import API, GamePlatformFixture, ROOT


class RepositoryTests(GamePlatformFixture):
    def request(self, role, method, path, **kwargs):
        self.client.cookies.clear()
        self.client.cookies.set("access_token", self.tokens[role])
        payload = dependencies.decode_token_payload(self.tokens[role])
        headers = {"Origin":"http://127.0.0.1",
                   "X-LanGame-CSRF":platform.csrf_token({"actor":f"{payload['role']}:{role}","session_id":payload["session_id"]}),
                   "Idempotency-Key":kwargs.pop("key",uuid.uuid4().hex)}
        return self.client.request(method,API+path,headers=headers,follow_redirects=False,**kwargs)

    def game_zip(self, key, version="1.0.0", **config_changes):
        with zipfile.ZipFile(ROOT/"docs/minigames/downloads/star-map.zip") as archive:
            members = {name:archive.read(name) for name in archive.namelist()}
        cfg = json.loads(members["config.json"])
        cfg.update(gameKey=key,version=version,**config_changes)
        definition = {"config":cfg,"writeback":json.loads(members[cfg["contracts"]["writeback"]]),
                      "rules":json.loads(members[cfg["contracts"]["rules"]])}
        members["config.json"] = validation.canonical(cfg).encode()
        members["js/offline-contracts.js"] = ("window.LanGameOfflineContracts = "+validation.canonical(definition)+";").encode()
        out=io.BytesIO()
        with zipfile.ZipFile(out,"w") as archive:
            for name,content in members.items():
                archive.writestr(name,content)
        return out.getvalue()

    def upload(self, uid, data, *, key=None, **opts):
        return self.request(uid,"POST","/admin/imports",key=key or uuid.uuid4().hex,data=opts,
                            files={"file":("sample.zip",data,"application/zip")})

    def approve_fixture(self, gid, vid):
        with platform.game_db() as conn:
            platform.db_start(conn,gid)
            packages.publish_reviewed(conn,gid,vid)
            conn.commit()

    def test_repository_rights_forks_versions_and_storage(self):
        raw = self.game_zip("student-sky",title="第一版",author="小天")
        opts = {"tags":json.dumps(["像素风"], ensure_ascii=False)}
        accepted=self.upload(931,raw,key="one",**opts)
        self.assertEqual(accepted.status_code,202,accepted.text)
        first=accepted.json()
        gid,vid=first["gameId"], first["versionId"]
        self.assertEqual(self.upload(931,raw,key="one",**opts).json(),first)
        self.assertEqual(self.upload(931,raw,**opts).json()["versionId"],vid)
        self.assertEqual(self.upload(932,raw,**opts).status_code,409)
        self.assertEqual(self.request(932,"GET","/admin/games/"+gid).status_code,404)
        # A teacher with the same numeric id must not inherit a student author's rights.
        with platform.game_db() as conn:
            self.assertEqual(packages.one(conn,"mg_games",gid)["owner_teacher_id"],-931)
            with self.assertRaises(HTTPException) as denied:
                platform.require_manager(conn, {"role":"teacher","id":931}, gid)
            self.assertEqual(denied.exception.status_code, 404)
        self.assertTrue(packages.worker_once())
        detail=self.request(931,"GET","/admin/games/"+gid).json()
        self.assertEqual((detail["game"]["title"], detail["versions"][0]["status"]),("第一版","reviewing"))
        self.assertEqual(detail["versions"][0]["review"]["status"],"queued")
        source="/games/"+gid+"/source"
        self.assertEqual(self.request(932,"GET",source).status_code,404)
        self.approve_fixture(gid,vid)
        package=self.request(932,"GET",source)
        self.assertEqual(package.content,raw)
        self.assertIn("attachment",package.headers["Content-Disposition"])
        fork=self.game_zip("student-remix",title="改编",author="小新")
        fork_reply=self.upload(932,fork,sourceVersionId=vid,tags='["3D"]')
        self.assertEqual(fork_reply.status_code,202,fork_reply.text)
        fork_info=self.request(932,"GET","/admin/games/"+fork_reply.json()["gameId"]).json()
        self.assertEqual(fork_info["versions"][0]["sourceAuthor"],"小天")
        self.assertEqual(fork_info["versions"][0]["tags"],["像素风","3D"])
        def change(method,suffix,**fields):
            rev=self.request(931,"GET","/admin/games/"+gid).json()["game"]["revision"]
            return self.request(931,method,"/admin/games/"+gid+suffix,json={"expectedRevision":rev,**fields})
        self.assertEqual(change("PATCH","",tags=["绕开审查"]).status_code,403)
        self.assertEqual(change("PATCH","/versions/"+vid+"/license",allowRemix=False).status_code,200)
        self.assertEqual(self.request(932,"GET",source).status_code,404)
        newer=self.upload(931,self.game_zip("student-sky","1.10.0",title="新版"),allowRemix="false")
        self.assertEqual(newer.json()["gameId"],gid)
        self.assertEqual(self.request(931,"GET","/admin/games/"+gid).json()["game"]["title"],"第一版")
        for _ in range(2):
            packages.worker_once()
        next_id=newer.json()["versionId"]
        self.approve_fixture(gid,next_id)
        info=self.request(931,"GET","/admin/games/"+gid).json()
        self.assertEqual([v["releaseVersion"] for v in info["versions"]],["1.10.0","1.0.0"])
        self.assertEqual(info["game"]["title"],"新版")
        self.assertEqual(change("PATCH","/versions/"+vid+"/license",allowRemix=True).status_code,200)
        self.assertEqual(self.request(932,"GET",source+"?version="+vid).content,raw)
        old_fork = self.game_zip("old-source-remix", attribution={"gameKey":"student-sky","version":"1.0.0",
                                 "author":"小天","tags":["像素风"]})
        historical=self.upload(932,old_fork)
        self.assertEqual(historical.status_code,202,historical.text)
        self.assertEqual(change("PATCH","/versions/"+vid+"/license",allowRemix=False).status_code,200)
        self.assertEqual(self.request(932,"GET",source+"?version="+vid).status_code,404)
        self.assertEqual(change("POST","/versions/"+next_id+"/retire").status_code,200)
        self.assertEqual(self.request(932,"GET",source).status_code,404)
        erase=change("DELETE","/versions/"+vid)
        self.assertEqual(erase.status_code,202,erase.text)
        packages.worker_once()
        self.assertFalse(packages.asset_path("version",gid,vid).exists())
        self.assertTrue(packages.asset_path("version",gid,next_id).is_dir())
        self.assertEqual(len(self.request(931,"GET","/admin/games/"+gid).json()["versions"]),1)
        ledger = platform.game_root().parent / "audit" / "minigame-deletions"
        digest, entries = recovery.register_snapshot(ledger)
        self.assertEqual(entries[0]["versionId"],vid)
        self.assertEqual(recovery.reconcile(ledger,digest)["versionsToErase"],0)
        self.assertEqual(self.upload(931,raw,key="one",**opts).status_code,410)

    def test_teacher_imports_cfg_before_browser_acceptance_and_invalid_manifest_leaves_no_draft(self):
        bad=self.upload(901,b"broken")
        self.assertEqual(bad.status_code,422)
        self.assertEqual(self.request(901,"GET","/admin/games").json()["items"],[])
        accepted=self.upload(901,self.game_zip("teacher-sky",title="天文"),allowRemix="false")
        self.assertEqual(accepted.status_code,202,accepted.text)
        game=accepted.json()["gameId"]
        with patch.dict("os.environ",{"MINIGAMES_PROBE_COMMAND":""}):
            packages.worker_once()
        detail=self.request(901,"GET","/admin/games/"+game).json()
        self.assertEqual((detail["game"]["title"],detail["jobs"][0]["status"]),("天文","waiting_probe"))
        self.assertFalse(detail["versions"][0]["allowRemix"])

    def test_detail_related_games_and_student_submit_review_lifecycle(self):
        raw = self.game_zip("student-origin", title="星际漫步", author="小天")
        first = self.upload(931, raw, tags='["像素风"]').json()
        gid, vid = first["gameId"], first["versionId"]
        self.assertTrue(packages.worker_once())
        self.approve_fixture(gid, vid)

        # Same owner creates a same-title edition and a remix; another student creates a private remix.
        sibling_res = self.upload(931, self.game_zip("student-origin-v2", title="星际漫步", author="小天"))
        self.assertEqual(sibling_res.status_code, 202, sibling_res.text)
        sibling = sibling_res.json()
        self.assertTrue(packages.worker_once())
        own_fork_res = self.upload(931, self.game_zip("student-own-fork", title="星际漫步改", author="小天"), sourceVersionId=vid)
        self.assertEqual(own_fork_res.status_code, 202, own_fork_res.text)
        own_fork = own_fork_res.json()
        self.assertTrue(packages.worker_once())
        other_fork_res = self.upload(932, self.game_zip("student-other-fork", title="星际漫步", author="小新"), sourceVersionId=vid)
        self.assertEqual(other_fork_res.status_code, 202, other_fork_res.text)
        other_fork = other_fork_res.json()
        self.assertTrue(packages.worker_once())

        detail = self.request(931, "GET", "/admin/games/" + gid).json()
        related_by_id = {item["id"]: item["relation"] for item in detail["relatedGames"]}
        self.assertEqual(related_by_id.get(own_fork["gameId"]), "remix")
        self.assertEqual(related_by_id.get(sibling["gameId"]), "same_title")
        # Unauthorized student games must never leak into relatedGames.
        self.assertNotIn(other_fork["gameId"], related_by_id)
        fork_detail = self.request(931, "GET", "/admin/games/" + own_fork["gameId"]).json()
        self.assertEqual(fork_detail["relatedGames"][0]["id"], gid)
        self.assertEqual(fork_detail["relatedGames"][0]["relation"], "source")

        def change(uid, game_id, method, suffix, **fields):
            rev = self.request(uid, "GET", "/admin/games/" + game_id).json()["game"]["revision"]
            return self.request(uid, method, "/admin/games/" + game_id + suffix, json={"expectedRevision": rev, **fields})

        # Unlist a listed student game, then resubmit the ready version for review and re-approve.
        self.assertEqual(change(931, gid, "POST", "/unlist").status_code, 200)
        unlisted_rev = self.request(931, "GET", "/admin/games/" + gid).json()["game"]["revision"]
        self.assertEqual(
            self.request(932, "POST", "/admin/games/" + gid + "/versions/" + vid + "/submit-review",
                         json={"expectedRevision": unlisted_rev}).status_code,
            404,
        )
        resubmitted = change(931, gid, "POST", "/versions/" + vid + "/submit-review")
        self.assertEqual(resubmitted.status_code, 200, resubmitted.text)
        after_resubmit = self.request(931, "GET", "/admin/games/" + gid).json()
        self.assertEqual(after_resubmit["versions"][0]["status"], "reviewing")
        self.assertEqual(after_resubmit["versions"][0]["review"]["status"], "queued")
        self.approve_fixture(gid, vid)
        relisted = self.request(931, "GET", "/admin/games/" + gid).json()
        self.assertEqual(relisted["game"]["status"], "listed")

        # Retire the version, then resubmit it for review again.
        self.assertEqual(change(931, gid, "POST", "/versions/" + vid + "/retire").status_code, 200)
        self.assertEqual(change(931, gid, "POST", "/versions/" + vid + "/submit-review").status_code, 200)

        # A version explicitly rejected by moderation cannot be resubmitted without a new package.
        with platform.game_db() as conn:
            conn.execute("UPDATE mg_versions SET status='rejected' WHERE id=?", (vid,))
            conn.execute("UPDATE mg_content_reviews SET status='rejected',reason='违规内容' WHERE version_id=?", (vid,))
            conn.commit()
        blocked = change(931, gid, "POST", "/versions/" + vid + "/submit-review")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["detail"]["code"], "REVIEW_REJECTED")

        # Teacher games reject submit-review with TEACHER_WORK.
        teacher_game = self.upload(901, self.game_zip("teacher-review-guard", title="教师试玩")).json()
        t_resp = change(901, teacher_game["gameId"], "POST", "/versions/" + teacher_game["versionId"] + "/submit-review")
        self.assertEqual(t_resp.status_code, 409)
        self.assertEqual(t_resp.json()["detail"]["code"], "TEACHER_WORK")
