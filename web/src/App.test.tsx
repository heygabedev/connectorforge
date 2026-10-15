import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, test, vi } from 'vitest'
import App from './App'

afterEach(() => vi.unstubAllGlobals())
function mount() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <App />
    </QueryClientProvider>,
  )
}

test('shows a usable sign-in form when the session is absent', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Sign in' }), { status: 401 })),
  )
  mount()
  expect(await screen.findByLabelText('Login code')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Continue to workspace' })).toBeVisible()
})

test('login failure is announced without losing the form', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({ detail: 'Code expired' }), { status: 401 })),
    ),
  )
  mount()
  await userEvent.type(await screen.findByLabelText('Login code'), 'invalid-code')
  await userEvent.click(screen.getByRole('button', { name: 'Continue to workspace' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Code expired')
})

test('viewer sees actual workspace values without administrative controls', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            path.endsWith('/auth/me')
              ? { role: 'viewer', csrf: 'test' }
              : {
                  name: 'Local workspace',
                  role: 'viewer',
                  version: '0.1.0',
                  audit_events: 3,
                  jobs: 2,
                },
          ),
        ),
      ),
    ),
  )
  mount()
  expect(await screen.findByRole('heading', { name: 'Workspace overview' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Access' })).not.toBeInTheDocument()
  expect(screen.getByText('0.1.0')).toBeVisible()
})

test('connectivity failures offer an explicit retry', async () => {
  const fetcher = vi.fn().mockRejectedValue(new Error('Service unavailable'))
  vi.stubGlobal('fetch', fetcher)
  mount()
  await userEvent.click(await screen.findByRole('button', { name: 'Reconnect' }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
})
