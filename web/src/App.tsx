import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  ArrowRight,
  Boxes,
  Check,
  ChevronRight,
  CircleDot,
  KeyRound,
  Layers3,
  LogOut,
  ShieldCheck,
  Terminal,
  Workflow,
} from 'lucide-react'
import { api, ApiError, setCsrf, type Key, type Session, type Workspace } from './api'

function Login({ onLogin }: { onLogin: (session: Session) => void }) {
  const [code, setCode] = useState('')
  const login = useMutation({
    mutationFn: () =>
      api<Session>('/auth/login', { method: 'POST', body: JSON.stringify({ code }) }),
    onSuccess: onLogin,
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    login.mutate()
  }
  return (
    <main className="login-page">
      <div className="login-story">
        <div className="brand">
          <span className="brand-mark">
            <Boxes size={24} />
          </span>
          ConnectorForge
        </div>
        <div className="eyebrow">YOUR INTEGRATIONS. UNDER CONTROL.</div>
        <h1>
          Build the connection.
          <br />
          <span>Trust the contract.</span>
        </h1>
        <p>
          A local workbench for inspecting API contracts, exercising failures, and shipping
          connector changes with evidence.
        </p>
        <div className="pipeline">
          <span>
            <Layers3 size={18} /> Specification
          </span>
          <ArrowRight size={16} />
          <span>
            <Workflow size={18} /> Connector
          </span>
          <ArrowRight size={16} />
          <span>
            <ShieldCheck size={18} /> Verified release
          </span>
        </div>
        <div className="local-note">
          <CircleDot size={14} /> Local workspace · Mock providers · No external connections
        </div>
      </div>
      <section className="login-card" aria-labelledby="login-heading">
        <span className="section-icon">
          <KeyRound />
        </span>
        <h2 id="login-heading">Open your workspace</h2>
        <p>Enter a one-time code from your local terminal.</p>
        <form onSubmit={submit}>
          <label htmlFor="login-code">Login code</label>
          <input
            id="login-code"
            autoComplete="off"
            type="password"
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="Paste your one-time code"
          />
          {login.error && (
            <p className="error" role="alert">
              {login.error.message}
            </p>
          )}
          <button className="primary full" disabled={login.isPending}>
            {login.isPending ? 'Opening workspace…' : 'Continue to workspace'}
            <ArrowRight size={16} />
          </button>
        </form>
        <div className="command">
          <Terminal size={15} />
          <code>connectorforge login-code</code>
        </div>
        <p className="caption">Codes expire after 10 minutes and can be used once.</p>
      </section>
    </main>
  )
}

function Access() {
  const cache = useQueryClient()
  const keys = useQuery({ queryKey: ['keys'], queryFn: () => api<Key[]>('/keys') })
  const [name, setName] = useState('')
  const [secret, setSecret] = useState('')
  const create = useMutation({
    mutationFn: () =>
      api<{ token: string }>('/keys', { method: 'POST', body: JSON.stringify({ name }) }),
    onSuccess: (result) => {
      setSecret(result.token)
      setName('')
      void cache.invalidateQueries({ queryKey: ['keys'] })
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/keys/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ['keys'] })
    },
  })
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">WORKSPACE SECURITY</div>
          <h1>Access credentials</h1>
          <p>Issue short-lived credentials for local tools. Secrets are shown once.</p>
        </div>
        <span className="tag">
          <ShieldCheck size={14} /> Admin
        </span>
      </div>
      <section className="panel">
        <h2>New API credential</h2>
        <p>Operator access for reading and invoking connector operations. Expires in 24 hours.</p>
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate()
          }}
        >
          <label className="grow">
            Credential name
            <input
              required
              maxLength={80}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Local integration"
            />
          </label>
          <button className="primary" disabled={create.isPending}>
            Create credential
          </button>
        </form>
        {secret && (
          <div className="secret-box">
            <p>
              <Check size={16} /> Copy this credential now. It cannot be retrieved later.
            </p>
            <code>{secret}</code>
            <button onClick={() => setSecret('')}>Dismiss</button>
          </div>
        )}
        {create.error && (
          <p role="alert" className="error">
            {create.error.message}
          </p>
        )}
      </section>
      <section className="panel">
        <h2>Issued credentials</h2>
        {keys.isPending ? (
          <p role="status">Loading credentials…</p>
        ) : keys.error ? (
          <p role="alert" className="error">
            {keys.error.message}
          </p>
        ) : !keys.data?.length ? (
          <div className="empty">
            <KeyRound />
            <h3>No API credentials yet</h3>
            <p>Create a credential when you connect a local tool.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {keys.data.map((key) => (
                  <tr key={key.id}>
                    <td>{key.name}</td>
                    <td>{key.role}</td>
                    <td>{key.revoked ? 'Revoked' : key.expired ? 'Expired' : 'Active'}</td>
                    <td>
                      <button
                        disabled={key.revoked || revoke.isPending}
                        onClick={() => revoke.mutate(key.id)}
                      >
                        Revoke<span className="sr-only"> {key.name}</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {revoke.error && (
          <p role="alert" className="error">
            {revoke.error.message}
          </p>
        )}
      </section>
    </>
  )
}

function Overview() {
  const workspace = useQuery({
    queryKey: ['workspace'],
    queryFn: () => api<Workspace>('/workspace'),
  })
  if (workspace.isPending) return <p role="status">Loading workspace…</p>
  if (workspace.error)
    return (
      <div className="panel error" role="alert">
        {workspace.error.message}
        <button onClick={() => void workspace.refetch()}>Try again</button>
      </div>
    )
  const data = workspace.data!
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">LOCAL CONTROL CENTER</div>
          <h1>Workspace overview</h1>
          <p>Your connector workspace, with every change accounted for.</p>
        </div>
        <span className="status">
          <span /> Workspace ready
        </span>
      </div>
      <div className="stats-grid">
        <section className="stat">
          <span>Application version</span>
          <strong>{data.version}</strong>
          <small>Local installation</small>
        </section>
        <section className="stat">
          <span>Audit events</span>
          <strong>{data.audit_events}</strong>
          <small>Recorded workspace activity</small>
        </section>
        <section className="stat">
          <span>Background jobs</span>
          <strong>{data.jobs}</strong>
          <small>Persisted in this workspace</small>
        </section>
      </div>
      <section className="panel welcome">
        <span className="section-icon">
          <Workflow />
        </span>
        <h2>A workspace you can inspect.</h2>
        <p>
          Activity is stored locally. Your current role is <strong>{data.role}</strong>; access is
          checked by the service on every request.
        </p>
        <div className="feature-row">
          <span>
            <Check size={16} /> Local storage
          </span>
          <span>
            <Check size={16} /> Scoped credentials
          </span>
          <span>
            <Check size={16} /> Audited access
          </span>
        </div>
      </section>
    </>
  )
}

export default function App() {
  const cache = useQueryClient()
  const [page, setPage] = useState('overview')
  const session = useQuery({
    queryKey: ['session'],
    queryFn: async () => {
      try {
        const value = await api<Session>('/auth/me')
        setCsrf(value.csrf)
        return value
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null
        throw error
      }
    },
  })
  const logout = useMutation({
    mutationFn: () => api('/auth/logout', { method: 'POST' }),
    onSuccess: () => {
      setCsrf(null)
      cache.clear()
      setPage('overview')
    },
  })
  if (session.isPending)
    return (
      <main className="loading" role="status">
        Opening ConnectorForge…
      </main>
    )
  if (session.error)
    return (
      <main className="loading">
        <p role="alert">{session.error.message}</p>
        <button onClick={() => void session.refetch()}>Reconnect</button>
      </main>
    )
  if (!session.data)
    return (
      <Login
        onLogin={(value) => {
          setCsrf(value.csrf)
          cache.setQueryData(['session'], value)
        }}
      />
    )
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Boxes size={21} />
          </span>
          ConnectorForge
        </div>
        <div className="workspace-switch">
          <span className="workspace-avatar">L</span>
          <div>
            Local workspace<small>Demo environment</small>
          </div>
          <ChevronRight size={14} />
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          <button
            className={page === 'overview' ? 'active' : ''}
            aria-current={page === 'overview' ? 'page' : undefined}
            onClick={() => setPage('overview')}
          >
            <Activity size={18} />
            Overview
          </button>
          {session.data.role === 'admin' && (
            <button
              className={page === 'access' ? 'active' : ''}
              aria-current={page === 'access' ? 'page' : undefined}
              onClick={() => setPage('access')}
            >
              <KeyRound size={18} />
              Access
            </button>
          )}
        </nav>
        <div className="sidebar-bottom">
          <span className="status">
            <span /> Running locally
          </span>
          <p>All provider data stays on this machine.</p>
          <div className="profile">
            <span className="workspace-avatar">{session.data.role[0].toUpperCase()}</span>
            <div>
              {session.data.role}
              <small>Local session</small>
            </div>
            <button
              aria-label="Sign out"
              disabled={logout.isPending}
              onClick={() => logout.mutate()}
            >
              <LogOut size={17} />
            </button>
          </div>
          {logout.error && (
            <p className="error" role="alert">
              {logout.error.message}
            </p>
          )}
        </div>
      </aside>
      <div className="main-column">
        <header className="topbar">
          <span>
            Workspace <ChevronRight size={14} /> {page === 'access' ? 'Access' : 'Overview'}
          </span>
          <span className="tag">DEMO / LOCAL</span>
          <button
            className="mobile-signout"
            aria-label="Sign out"
            disabled={logout.isPending}
            onClick={() => logout.mutate()}
          >
            <LogOut size={17} />
          </button>
        </header>
        <main id="main" className="content">
          {page === 'access' && session.data.role === 'admin' ? <Access /> : <Overview />}
        </main>
        <footer>
          ConnectorForge <span>Built for inspectable integrations.</span>
        </footer>
      </div>
    </div>
  )
}
