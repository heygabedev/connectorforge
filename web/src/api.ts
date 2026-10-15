export type Session = { role: 'admin' | 'operator' | 'viewer'; csrf: string | null }
export type Workspace = {
  name: string
  role: string
  version: string
  audit_events: number
  jobs: number
}
export type Key = {
  id: string
  name: string
  role: string
  expires_at: number
  revoked: boolean
  expired: boolean
}

let csrf: string | null = null
export function setCsrf(value: string | null) {
  csrf = value
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  if (csrf) headers.set('X-CSRF-Token', csrf)
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers,
    credentials: 'same-origin',
  })
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}))
    throw new ApiError(response.status, problem.detail ?? `Request failed (${response.status})`)
  }
  return response.status === 204 ? (undefined as T) : (response.json() as Promise<T>)
}
