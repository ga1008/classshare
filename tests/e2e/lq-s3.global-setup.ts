import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { readS3Fixture } from './fixtures/lq-s3';

// Login rotates the user's active session. Independent runners sharing these
// accounts invalidate each other even when their domain records are distinct.
export default async function setup() {
  const fixture = readS3Fixture();
  const lock = path.join(fixture.runtimeRoot, '.lq-s3-e2e.lock');
  const ownership = JSON.stringify({ pid: process.pid, id: crypto.randomUUID(), startedAt: new Date().toISOString() });
  let fd: number;
  try { fd = fs.openSync(lock, 'wx'); }
  catch (error: any) {
    if (error.code === 'EEXIST') throw Error('Another S3 runner owns this synthetic fixture. Run serially or seed a separate runtime. Inspect a stale lock before removing it.');
    throw error;
  }
  try { fs.writeFileSync(fd, ownership); } finally { fs.closeSync(fd); }
  return async () => {
    if (fs.existsSync(lock) && fs.readFileSync(lock, 'utf8') === ownership) fs.unlinkSync(lock);
  };
}
