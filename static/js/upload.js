/**
 * upload.js
 * Handles Chunked File Upload logic
 */

import { apiFetch } from './api.js';
import { formatSize, showToast } from './ui.js';

export class ChunkedUploader {
    constructor(file, courseId, options = {}) {
        this.file = file;
        this.courseId = courseId;
        this.chunkSize = 5 * 1024 * 1024; // 5MB
        this.uploadId = null;
        this.totalChunks = 0;
        this.uploadedChunks = 0;
        this.startTime = null;
        this.description = options.description || '';
        this.onProgress = options.onProgress || (() => {});
        this.onComplete = options.onComplete || (() => {});
        this.onError = options.onError || (() => {});
        this.aborted = false;
        this.concurrency = options.concurrency || 3;
    }

    async start() {
        this.startTime = Date.now();

        // 1. Pre-check for rapid resume (Second Transfer Logic)
        try {
            const checkData = await apiFetch('/api/files/check', {
                method: 'POST',
                body: {
                    file_name: this.file.name,
                    file_size: this.file.size,
                    course_id: this.courseId
                }
            });

            if (checkData && checkData.exists) {
                this.onComplete({
                    skipped: true,
                    inCurrentCourse: checkData.in_current_course,
                    linked: checkData.linked || false,
                    existingFile: checkData.file,
                    message: checkData.in_current_course
                        ? `"${this.file.name}" 当前课程已存在，跳过上传`
                        : `"${this.file.name}" 极速秒传成功！`
                });
                return;
            }
        } catch (e) {
            console.warn('Pre-check failed, continuing with full upload:', e);
        }

        // 2. Initialize Chunked Upload
        try {
            const initData = await apiFetch('/api/files/upload/init', {
                method: 'POST',
                body: {
                    file_name: this.file.name,
                    file_size: this.file.size,
                    course_id: this.courseId,
                    description: this.description
                }
            });

            this.uploadId = initData.upload_id;
            this.chunkSize = initData.chunk_size;
            this.totalChunks = initData.total_chunks;
        } catch (e) {
            this.onError(e);
            return;
        }

        // 3. Concurrent Upload Pool
        let currentIndex = 0;
        let activeUploads = 0;
        let hasError = false;

        const uploadNext = async () => {
            if (hasError || this.aborted || currentIndex >= this.totalChunks) return;
            const index = currentIndex++;
            activeUploads++;

            const start = index * this.chunkSize;
            const end = Math.min(start + this.chunkSize, this.file.size);
            const chunkBlob = this.file.slice(start, end);

            try {
                await this._uploadChunkWithRetry(index, chunkBlob, 3); // Max 3 retries
                this.uploadedChunks++;

                const progress = this.uploadedChunks / this.totalChunks;
                const elapsed = (Date.now() - this.startTime) / 1000;
                const bytesUploaded = Math.min(this.uploadedChunks * this.chunkSize, this.file.size);
                const speed = bytesUploaded / elapsed;

                this.onProgress({
                    percent: Math.min(Math.round(progress * 100), 99), // Leave 1% for backend merge time
                    speed: speed,
                    remainingSeconds: speed > 0 ? (this.file.size - bytesUploaded) / speed : 0,
                    fileName: this.file.name
                });
            } catch (e) {
                hasError = true;
                this.onError(e);
            } finally {
                activeUploads--;
                await uploadNext();
            }
        };

        // Start Concurrent Workers
        const workers = [];
        for (let i = 0; i < Math.min(this.concurrency, this.totalChunks); i++) {
            workers.push(uploadNext());
        }
        await Promise.all(workers);

        if (hasError || this.aborted) return;

        // 4. Complete Upload & Merge
        try {
            const result = await apiFetch('/api/files/upload/complete', {
                method: 'POST',
                body: { upload_id: this.uploadId }
            });

            result.skipped = false;
            this.onComplete(result);
        } catch (e) {
            this.onError(e);
        }
    }

    async _uploadChunkWithRetry(index, blob, maxRetries) {
        for (let i = 0; i < maxRetries; i++) {
            try {
                await this._uploadChunk(index, blob);
                return;
            } catch (e) {
                if (i === maxRetries - 1) throw e;
                console.warn(`Chunk ${index} upload failed, retrying (${i + 1}/${maxRetries})...`);
                await new Promise(r => setTimeout(r, 1000 * (i + 1))); // Exponential backoff
            }
        }
    }

    _uploadChunk(index, blob) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            const formData = new FormData();
            formData.append('upload_id', this.uploadId);
            formData.append('chunk_index', index.toString());
            formData.append('chunk', blob, `chunk_${index}`);

            xhr.open('POST', '/api/files/upload/chunk');
            xhr.onload = () => {
                if (xhr.status >= 200 && xhr.status < 300) resolve();
                else reject(new Error(`Chunk ${index} failed (${xhr.status})`));
            };
            xhr.onerror = () => reject(new Error('Network Error'));
            xhr.ontimeout = () => reject(new Error('Timeout'));
            xhr.timeout = 60000; // 60 seconds
            xhr.send(formData);
        });
    }

    abort() {
        this.aborted = true;
    }
}
