/**
 * miniprogram-ci 自动化：预览二维码 / 上传体验版。
 *
 * 用法：
 *   npm run mp:preview                  # 构建产物生成预览二维码 preview-qr.png
 *   npm run mp:upload -- 1.0.0 "说明"   # 上传为体验版
 *
 * 前置：小程序后台"开发设置 → 小程序代码上传"已生成密钥，
 * 密钥文件 private.<appid>.key 放在仓库根目录，IP 白名单关闭或已加白。
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import ci from "miniprogram-ci";

const here = path.dirname(fileURLToPath(import.meta.url));
const miniappRoot = path.resolve(here, "..");
const repoRoot = path.resolve(miniappRoot, "..");
const projectPath = path.join(miniappRoot, "dist", "build", "mp-weixin");

function fail(message) {
  console.error(`[mp-ci] ${message}`);
  process.exit(1);
}

if (!existsSync(projectPath)) {
  fail("dist/build/mp-weixin 不存在，请先 npm run build:mp-weixin");
}

const projectConfig = JSON.parse(
  readFileSync(path.join(projectPath, "project.config.json"), "utf-8"),
);
const appid = projectConfig.appid;
if (!appid || appid.startsWith("touristappid")) {
  fail("产物 project.config.json 缺少有效 appid，请检查 src/manifest.json");
}

const keyPath = path.join(repoRoot, `private.${appid}.key`);
if (!existsSync(keyPath)) {
  fail(`未找到上传密钥 ${keyPath}，请从小程序后台下载后放到仓库根目录`);
}

const project = new ci.Project({
  appid,
  type: "miniProgram",
  projectPath,
  privateKeyPath: keyPath,
  ignores: ["node_modules/**/*"],
});

const action = process.argv[2] || "preview";

if (action === "preview") {
  const qrPath = path.join(miniappRoot, "preview-qr.png");
  await ci.preview({
    project,
    desc: "本地预览",
    setting: { es6: true, minify: true },
    qrcodeFormat: "image",
    qrcodeOutputDest: qrPath,
  });
  console.log(`[mp-ci] 预览二维码已生成: ${qrPath}`);
} else if (action === "upload") {
  const version = process.argv[3] || "0.0.1";
  const desc = process.argv[4] || "CI 自动上传";
  const result = await ci.upload({
    project,
    version,
    desc,
    setting: { es6: true, minify: true },
  });
  console.log(`[mp-ci] 已上传体验版 v${version}`, result.subPackageInfo ?? "");
} else {
  fail(`未知操作 ${action}，支持 preview / upload`);
}
