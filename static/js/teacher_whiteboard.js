/**
 * 讲课白板入口（thin shim）。
 * 实现拆分在 ./whiteboard/：board.js（教师讲课白板）、exam_board.js（考试答题附图板）。
 * 宿主：material_render_shell.js / material_viewer.js 动态 import 本文件；exam_take.html 直接 import。
 * 设计真源：docs/whiteboard-upgrade-2026-09.md
 */
import { initTeacherWhiteboard } from './whiteboard/board.js';
import { initExamDrawingWhiteboard } from './whiteboard/exam_board.js';

function bootstrap() {
    initTeacherWhiteboard(window.MATERIAL_VIEWER_CONTEXT || {});
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap, { once: true });
} else {
    bootstrap();
}

export { initTeacherWhiteboard, initExamDrawingWhiteboard };
