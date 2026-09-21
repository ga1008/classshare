# Static asset publication and first upgrade

Build the replacement application image before changing the running app. Then
run the tracked migration helper with the same Compose command and options used
by the deployment, for example:

```sh
bash deployment/docker/build_app.sh docker compose -f docker-compose.yml
bash deployment/docker/seed_previous_static_assets.sh docker compose -f docker-compose.yml
```

Only proceed with the normal quiesced database migration and application
replacement if the seed helper exits successfully. The local ignored
`deployment/deploy_remote.ps1` calls this helper at that point; its host-specific
settings are intentionally not distributed. Other deployment entry points must
honor this same gate. A direct first-upgrade `docker compose up` is insufficient.

The helper reads the old container's `/app/static/dist/assets/.` through
`docker cp`, then uses the replacement image's `tools/publish_static_assets.py`
to validate and publish that tar stream into the shared `lanshare-static` volume.
It accepts only flat, hashed Vite code assets. It does not extract tar files or
read runtime/user data. The seed command overrides the regular image entrypoint,
so it also skips that entrypoint's runtime-directory initialization. A missing
old container is a fresh install; multiple
containers, export failure, an empty export, unsafe members or conflicting bytes
fail the gate. No app is stopped or replaced by this helper.

The main app then publishes its complete current native graph and Vite assets
before serving requests. Publication is additive: keep the shared static volume
during upgrade and rollback so an already-open page can load its original lazy
chunks. Do not remove old graphs until the active-page lifetime and rollback
window have been established. The native graph currently occupies about 10 MiB
per changed release plus precompressed copies; measure volume growth operationally.

Before a production release, validate `nginx -t`, shared-volume permissions,
`gzip_static` and immutable headers, then keep an old browser tab open through a
two-release cutover and exercise a previously unrequested lazy chunk. Unit and
mock-command tests cover publication and the cutover gate; they do not certify
Docker/nginx behavior on the target host.
