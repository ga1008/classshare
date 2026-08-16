/**
 * 鉴权附件预览：uni.downloadFile 带 bearer 头拉到本地临时文件，
 * 图片走 previewImage（可缩放保存），文档走 openDocument。
 * 服务器鉴权不降级——没有裸链接，一切经 Authorization 头。
 */
import { API_BASE } from "../config";
import { getStoredToken } from "./api";

const DOC_EXTENSIONS = ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt"];

function isImageFile(fileName: string, mimeType: string): boolean {
  if (mimeType.startsWith("image/")) return true;
  const ext = (fileName.split(".").pop() || "").toLowerCase();
  return ["png", "jpg", "jpeg", "gif", "webp", "bmp"].includes(ext);
}

function docFileType(fileName: string): string | null {
  const ext = (fileName.split(".").pop() || "").toLowerCase();
  return DOC_EXTENSIONS.includes(ext) ? ext : null;
}

function downloadAuthed(path: string): Promise<string> {
  const header: Record<string, string> = {};
  const token = getStoredToken();
  if (token) header.Authorization = `Bearer ${token}`;
  return new Promise((resolve, reject) => {
    uni.downloadFile({
      url: `${API_BASE}${path}`,
      header,
      timeout: 60000,
      success: (res) => {
        if (res.statusCode === 200 && res.tempFilePath) {
          resolve(res.tempFilePath);
        } else {
          reject(new Error(`下载失败（${res.statusCode}）`));
        }
      },
      fail: () => reject(new Error("下载失败，请检查网络后重试。")),
    });
  });
}

/** 下载并预览一个受保护附件。localPath 命中时跳过下载直接预览。 */
export async function previewProtectedFile(options: {
  path: string;
  fileName: string;
  mimeType?: string;
  localPath?: string;
}): Promise<void> {
  const { path, fileName, mimeType = "", localPath } = options;
  try {
    uni.showLoading({ title: "加载附件…", mask: true });
    const filePath = localPath || (await downloadAuthed(path));
    uni.hideLoading();
    if (isImageFile(fileName, mimeType)) {
      uni.previewImage({ urls: [filePath] });
      return;
    }
    const fileType = docFileType(fileName);
    if (fileType) {
      uni.openDocument({
        filePath,
        fileType,
        showMenu: true,
        fail: () => uni.showToast({ title: "该文件类型无法预览", icon: "none" }),
      });
      return;
    }
    uni.showToast({ title: "该文件类型请在网页端查看", icon: "none" });
  } catch (error: unknown) {
    uni.hideLoading();
    uni.showToast({
      title: error instanceof Error ? error.message : "预览失败",
      icon: "none",
    });
  }
}
