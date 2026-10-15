import { spawn } from 'node:child_process'
import { fileURLToPath, URL } from 'node:url'
import process from 'node:process'
import { resolve } from 'node:path'

const root = fileURLToPath(new URL('../../', import.meta.url))
const python = resolve(
  root,
  process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python',
)
const child = spawn(python, ['-m', 'connectorforge.cli', 'serve'], {
  cwd: root,
  env: process.env,
  stdio: 'inherit',
})
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal))
child.on('exit', (code) => process.exit(code ?? 1))
