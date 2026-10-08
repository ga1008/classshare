import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
const output=path.resolve(process.env.CLASSROOM_AVAILABILITY_OUTPUT || '.codex-temp/classroom-availability-20261008-qa');
export default defineConfig({ testDir:'./specs',testMatch:'classroom-availability.spec.ts',workers:1,fullyParallel:false,retries:0,timeout:90_000,
  outputDir:path.join(output,'test-results'),reporter:[['list'],['json',{outputFile:path.join(output,'results.json')}]],
  use:{...devices['Desktop Chrome'],channel:'chrome',baseURL:'http://127.0.0.1:8360',actionTimeout:10_000,screenshot:'only-on-failure',trace:'retain-on-failure'},
  projects:[{name:'desktop',use:{viewport:{width:1440,height:980}}},{name:'touch',use:{viewport:{width:390,height:844},isMobile:true,hasTouch:true}}],
});
