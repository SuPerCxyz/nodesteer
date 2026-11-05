import { readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { spawn } from 'node:child_process'

/**
 * Vitest 4 浏览器模式在单个浏览器会话中连续运行较多测试文件后，会出现
 * `[vite] server connection lost`，随后下一个文件的动态 import 失败并报
 * `Failed to fetch dynamically imported module` / `Cannot connect to the iframe`。
 * 单文件运行稳定；按文件分批重启 Vitest 进程可避免该基础设施 flake。
 *
 * 这里把测试文件切成小批，每批启动一个独立的 Vitest 浏览器会话；失败批次最多重试
 * 4 次，以区分真正的测试失败和浏览器会话偶发断连。所有批次都通过才算成功。
 */
const BATCH_SIZE = Math.max(1, Number(process.env.VITEST_BATCH_SIZE || 1))
const MAX_ATTEMPTS = 5
const TEST_FILE_PATTERN = /\.(test|spec)\.(ts|tsx)$/

function collectTestFiles(directory) {
  const files = []
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === 'dist') continue
    const path = join(directory, entry.name)
    if (entry.isDirectory()) {
      files.push(...collectTestFiles(path))
    } else if (TEST_FILE_PATTERN.test(entry.name)) {
      files.push(path)
    }
  }
  return files
}

function chunk(items, size) {
  const batches = []
  for (let index = 0; index < items.length; index += size) {
    batches.push(items.slice(index, index + size))
  }
  return batches
}

const BATCH_TIMEOUT_MS = 20_000

function runVitest(files) {
  const vitest = resolve('node_modules/vitest/vitest.mjs')
  console.log('[test] spawning vitest for', files.length, 'file(s)')
  return new Promise((resolvePromise) => {
    let settled = false
    const finish = (code) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolvePromise(code)
    }
    const child = spawn(
      process.execPath,
      [vitest, 'run', '--browser.headless', ...files],
      { stdio: ['ignore', 'inherit', 'inherit'], env: process.env }
    )
    // 浏览器会话偶发卡死时不能让整个 npm test 永久挂起。
    const timer = setTimeout(() => {
      console.error(`[test] batch timed out after ${BATCH_TIMEOUT_MS}ms`)
      child.kill('SIGKILL')
      finish(1)
    }, BATCH_TIMEOUT_MS)
    child.on('error', (error) => {
      console.error('[test] failed to start vitest:', error)
      finish(1)
    })
    child.on('exit', (code, signal) => {
      if (settled) return
      if (signal) {
        console.error(`[test] vitest exited by signal ${signal}`)
        finish(1)
        return
      }
      finish(code ?? 1)
    })
  })
}

const testFiles = collectTestFiles(resolve('src')).sort()
if (testFiles.length === 0) {
  console.error('[test] no test files found under src/')
  process.exit(1)
}

const batches = chunk(testFiles, BATCH_SIZE)
let failedBatches = 0
let retries = 0

console.log(
  `[test] ${testFiles.length} files in ${batches.length} batches (max ${BATCH_SIZE} files per batch)`
)

for (let index = 0; index < batches.length; index++) {
  const files = batches[index]
  let exitCode = 1
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    if (attempt > 1) {
      retries++
      console.log(
        `[test] batch ${index + 1}/${batches.length} failed; retrying (attempt ${attempt}/${MAX_ATTEMPTS})`
      )
    }
    exitCode = await runVitest(files)
    if (exitCode === 0) break
  }
  if (exitCode !== 0) {
    failedBatches++
    console.error(
      `[test] batch ${index + 1}/${batches.length} failed after ${MAX_ATTEMPTS} attempts`
    )
  }
}

console.log(
  `[test] finished: ${batches.length - failedBatches}/${batches.length} batches passed, ${retries} retries`
)
process.exit(failedBatches === 0 ? 0 : 1)
