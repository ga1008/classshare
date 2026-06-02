import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  publicDir: false,
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: false,
    cors: true,
    origin: 'http://127.0.0.1:5173',
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, 'frontend/src'),
    },
  },
  build: {
    outDir: 'static/dist',
    emptyOutDir: true,
    manifest: 'manifest.json',
    rollupOptions: {
      input: {
        'app-shell': resolve(__dirname, 'frontend/src/islands/app-shell.tsx'),
        'assignment-authoring-sync': resolve(__dirname, 'frontend/src/islands/assignment-authoring-sync.tsx'),
        'assignment-submit-sync': resolve(__dirname, 'frontend/src/islands/assignment-submit-sync.tsx'),
        'assignment-task-board-sync': resolve(__dirname, 'frontend/src/islands/assignment-task-board-sync.tsx'),
        'blog-launcher': resolve(__dirname, 'frontend/src/islands/blog-launcher.tsx'),
        'blog-topbar-sync': resolve(__dirname, 'frontend/src/islands/blog-topbar-sync.tsx'),
        'classroom-activity-workspace-sync': resolve(__dirname, 'frontend/src/islands/classroom-activity-workspace-sync.tsx'),
        'dashboard-quick-actions': resolve(__dirname, 'frontend/src/islands/dashboard-quick-actions.tsx'),
        'exam-assign-sync': resolve(__dirname, 'frontend/src/islands/exam-assign-sync.tsx'),
        'feedback-launcher': resolve(__dirname, 'frontend/src/islands/feedback-launcher.tsx'),
        'learning-progress-sync': resolve(__dirname, 'frontend/src/islands/learning-progress-sync.tsx'),
        'material-learning-path-sync': resolve(__dirname, 'frontend/src/islands/material-learning-path-sync.tsx'),
        'message-center-workspace-sync': resolve(__dirname, 'frontend/src/islands/message-center-workspace-sync.tsx'),
        'message-center-sync': resolve(__dirname, 'frontend/src/islands/message-center-sync.tsx'),
        'profile-launcher': resolve(__dirname, 'frontend/src/islands/profile-launcher.tsx'),
        'resource-workspace-sync': resolve(__dirname, 'frontend/src/islands/resource-workspace-sync.tsx'),
        'student-security-sync': resolve(__dirname, 'frontend/src/islands/student-security-sync.tsx'),
        'submission-jump-nav': resolve(__dirname, 'frontend/src/islands/submission-jump-nav.tsx'),
        'teacher-submission-workbench-sync': resolve(__dirname, 'frontend/src/islands/teacher-submission-workbench-sync.tsx'),
      },
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
  test: {
    include: ['frontend/src/**/*.test.ts'],
    exclude: ['node_modules', 'dist', 'static/dist', '.codex-temp', 'data', 'venv'],
  },
});
