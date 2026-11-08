"""Deployment guards for 200 users retaining two classrooms and a dashboard."""
from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest

import yaml

from tools import platform_capacity_linux_lab as lab


ROOT = Path(__file__).resolve().parents[1]


class RecorderConnectionBudgetTests(unittest.TestCase):
    def test_product_and_lab_share_finite_connection_budget_without_more_db_or_workers(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        services = compose["services"]
        for name in ("app", "ai", "mailer", "blog-crawler", "scheduler", "agent-worker", "nginx"):
            self.assertEqual({"nofile": {"soft": 8192, "hard": 8192}}, services[name]["ulimits"])
        app = services["app"]
        self.assertEqual("${LANSHARE_APP_MAX_CONNECTIONS:-2048}", app["environment"]["MAIN_LIMIT_CONCURRENCY"])
        self.assertEqual("12", app["environment"]["POSTGRES_POOL_MAX"])
        self.assertEqual(["main"], app["command"])
        self.assertNotIn("deploy", app)
        assignments = {node.targets[0].id: node.value for node in ast.parse((ROOT / "classroom_app/config.py").read_text(encoding="utf-8")).body
                       if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)}
        for name, expected in (("MAIN_LIMIT_CONCURRENCY", 2048), ("MAIN_WORKERS", 1), ("MAIN_THREADPOOL_TOKENS", 32)):
            getenv = next(node for node in ast.walk(assignments[name]) if isinstance(node, ast.Call)
                          and isinstance(node.func, ast.Attribute) and node.func.attr == "getenv")
            self.assertEqual(expected, ast.literal_eval(getenv.args[1]))
        self.assertEqual(2048, lab.SERVER_CONFIG["uvicorn_limit_concurrency"])
        self.assertEqual(1, lab.SERVER_CONFIG["workers"])
        self.assertEqual(12, lab.SERVER_CONFIG["postgres_pool_max"])

    def test_proxy_master_preserves_image_defaults_except_reviewed_fd_and_connection_limits(self):
        actual = (ROOT / "deployment/nginx-main.conf").read_text(encoding="utf-8")
        # These are the nginx:1.27-alpine effective defaults read from the
        # current deployment before this change. Keep HTTP includes/logs/mime,
        # sendfile, auto workers and keepalive intact when raising FD budgets.
        expected = '''
user  nginx;
worker_processes  auto;

error_log  /var/log/nginx/error.log notice;
pid        /run/nginx.pid;


events {
    worker_connections  1024;
}


http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    log_format  main  '$remote_addr - $remote_user [$time_local] "$request" '
                      '$status $body_bytes_sent "$http_referer" '
                      '"$http_user_agent" "$http_x_forwarded_for"';

    access_log  /var/log/nginx/access.log  main;

    sendfile        on;
    #tcp_nopush     on;

    keepalive_timeout  65;

    #gzip  on;

    include /etc/nginx/conf.d/*.conf;
}

'''
        restored = actual.replace("worker_rlimit_nofile  8192;\n", "").replace("worker_connections  4096;", "worker_connections  1024;")
        self.assertEqual(expected, restored)
        self.assertEqual(["8192"], re.findall(r"^worker_rlimit_nofile\s+(\d+);", actual, re.M))
        self.assertEqual(["4096"], re.findall(r"^\s*worker_connections\s+(\d+);", actual, re.M))
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        mounts = compose["services"]["nginx"]["volumes"]
        for mount in ("./deployment/nginx-main.conf:/etc/nginx/nginx.conf:ro", "./nginx.conf:/etc/nginx/conf.d/default.conf:ro",
                      "./tools/guardianangel.net.cn_nginx:/etc/nginx/ssl:ro", "lanshare-static:/srv/lanshare/static:ro"):
            self.assertIn(mount, mounts)

    def test_three_tab_model_has_http_and_proxy_fd_reserve_even_on_one_nginx_worker(self):
        # Two classroom tabs each retain class+social; a dashboard retains
        # another social socket. Hidden tabs retain all five sockets.
        websockets = 200 * (2 * 2 + 1)
        simultaneous_http = 200
        capacity = lab.SERVER_CONFIG["uvicorn_limit_concurrency"]
        self.assertLess(websockets + simultaneous_http, capacity)
        main = (ROOT / "deployment/nginx-main.conf").read_text(encoding="utf-8")
        proxy_connections = int(re.search(r"worker_connections\s+(\d+);", main)[1])
        self.assertLess(2 * (websockets + simultaneous_http), proxy_connections)
        self.assertLess(proxy_connections + 256, 8192)
        self.assertLess(capacity + lab.SERVER_CONFIG["postgres_pool_max"] + 256, 8192)
        self.assertLessEqual(capacity, 2048)


if __name__ == "__main__":
    unittest.main()
