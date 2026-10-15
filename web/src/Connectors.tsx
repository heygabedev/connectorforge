import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Boxes, FileCode2, Play, Plus, Save } from 'lucide-react'
import { api, type Session } from './api'

type Connector = { slug: string; name: string; paused: boolean; generation: number }
type Release = { id: string; version: string; operations: number }
type Operation = { id: string; method: string; path: string }
type Bundle = {
  version: string
  compiled: { source: unknown; operations: Operation[] }
  fixtures: unknown
  mappings: unknown
}
type Preview = {
  data: unknown
  meta: { status: number; isolated: boolean; version: string }
  timeline: { stage: string; status: string; elapsed_ms?: number }[]
}

const example = {
  openapi: '3.1.0',
  info: { title: 'Inventory demo', version: '1.0.0' },
  paths: {
    '/items': {
      get: {
        operationId: 'listItems',
        responses: {
          '200': {
            description: 'Demo items',
            content: {
              'application/json': {
                schema: {
                  type: 'array',
                  items: {
                    type: 'object',
                    required: ['id', 'name'],
                    properties: { id: { type: 'integer' }, name: { type: 'string' } },
                    additionalProperties: false,
                  },
                },
              },
            },
          },
        },
      },
    },
  },
}
const exampleFixtures = { listItems: { status: 200, body: [{ id: 1, name: 'Demo item' }] } }
const pretty = (value: unknown) => JSON.stringify(value, null, 2)

function Editor({
  slug,
  release,
  bundle,
  role,
  saved,
}: {
  slug: string
  release?: Release
  bundle?: Bundle
  role: Session['role']
  saved: (id: string) => void
}) {
  const [source, setSource] = useState(pretty(bundle?.compiled.source ?? example))
  const [fixtures, setFixtures] = useState(pretty(bundle?.fixtures ?? exampleFixtures))
  const [mappings, setMappings] = useState(pretty(bundle?.mappings ?? {}))
  const [version, setVersion] = useState(
    release ? release.version.replace(/\d+$/, (value) => String(Number(value) + 1)) : '1.0.0',
  )
  const [operation, setOperation] = useState(bundle?.compiled.operations[0]?.id ?? 'listItems')
  const [args, setArgs] = useState('{}')
  const [fault, setFault] = useState('none')
  const [fileError, setFileError] = useState('')
  const save = useMutation({
    mutationFn: () =>
      api<{ id: string }>(`/connectors/${slug}/releases`, {
        method: 'POST',
        body: pretty({
          source,
          version,
          fixtures: JSON.parse(fixtures),
          mappings: JSON.parse(mappings),
        }),
      }),
    onSuccess: (value) => saved(value.id),
  })
  const preview = useMutation({
    mutationFn: () =>
      api<Preview>(`/connectors/${slug}/releases/${release!.id}/preview`, {
        method: 'POST',
        body: pretty({ operation_id: operation, arguments: JSON.parse(args), fault }),
      }),
  })
  async function upload(file?: File) {
    if (!file) return
    if (file.size > 1024 * 1024) {
      setFileError('Choose a specification smaller than 1 MiB.')
      return
    }
    try {
      setSource(await file.text())
      setFileError('')
    } catch {
      setFileError('The file could not be read.')
    }
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    save.mutate()
  }
  return (
    <>
      {role === 'admin' && (
        <section className="panel">
          <div className="section-heading">
            <div>
              <h2>
                <FileCode2 size={18} /> Contract workbench
              </h2>
              <p>Each save creates an immutable version. JSON and safe YAML are supported.</p>
            </div>
            {release && <span className="tag">Editing from {release.version}</span>}
          </div>
          <form onSubmit={submit}>
            <div className="form-grid">
              <label>
                Next version
                <input
                  value={version}
                  required
                  pattern="[1-9][0-9]*\.[0-9]+\.[0-9]+"
                  onChange={(e) => setVersion(e.target.value)}
                />
              </label>
              <label>
                Import a specification
                <input
                  type="file"
                  accept=".json,.yaml,.yml"
                  onChange={(e) => void upload(e.target.files?.[0])}
                />
              </label>
            </div>
            {fileError && (
              <p role="alert" className="error">
                {fileError}
              </p>
            )}
            <label>
              OpenAPI specification
              <textarea
                className="code-editor"
                rows={15}
                spellCheck={false}
                value={source}
                onChange={(e) => setSource(e.target.value)}
              />
            </label>
            <div className="form-grid">
              <label>
                Response fixtures
                <textarea
                  className="code-editor"
                  rows={10}
                  spellCheck={false}
                  value={fixtures}
                  onChange={(e) => setFixtures(e.target.value)}
                />
              </label>
              <label>
                Field mappings
                <textarea
                  className="code-editor"
                  rows={10}
                  spellCheck={false}
                  value={mappings}
                  onChange={(e) => setMappings(e.target.value)}
                />
              </label>
            </div>
            <p className="caption">
              Fixtures map operation IDs to a status, body, optional headers, and delay_ms. Mappings
              select fields using JSON Pointers.
            </p>
            <button className="primary" disabled={save.isPending}>
              <Save size={16} />
              {save.isPending ? 'Compiling release…' : 'Save connector release'}
            </button>
            {save.error && (
              <p className="error" role="alert">
                {save.error.message}
              </p>
            )}
          </form>
        </section>
      )}
      {release && bundle && (
        <section className="panel">
          <div className="section-heading">
            <div>
              <h2>Run an isolated preview</h2>
              <p>Use the saved contract and fixtures. No provider network connection is opened.</p>
            </div>
            <span className="tag">{release.version}</span>
          </div>
          {role !== 'viewer' ? (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                preview.mutate()
              }}
            >
              <div className="form-grid">
                <label>
                  Operation
                  <select value={operation} onChange={(e) => setOperation(e.target.value)}>
                    {bundle.compiled.operations.map((op) => (
                      <option key={op.id} value={op.id}>
                        {op.method} {op.path} · {op.id}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Fault profile
                  <select value={fault} onChange={(e) => setFault(e.target.value)}>
                    {[
                      'none',
                      'throttle',
                      'timeout',
                      'malformed',
                      'unavailable',
                      'disconnect',
                      'schema_drift',
                      'expired',
                    ].map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                </label>
              </div>
              <label>
                Arguments (path, query, header, body)
                <textarea
                  rows={4}
                  className="code-editor"
                  value={args}
                  onChange={(e) => setArgs(e.target.value)}
                  spellCheck={false}
                />
              </label>
              <button className="primary" disabled={preview.isPending}>
                <Play size={16} />
                {preview.isPending ? 'Running preview…' : 'Run preview'}
              </button>
            </form>
          ) : (
            <p>Preview execution requires operator access.</p>
          )}
          {preview.error && (
            <p role="alert" className="error">
              {preview.error.message}
            </p>
          )}
          {preview.data && (
            <div aria-live="polite">
              <div className="result-heading">
                <span className="status">
                  <span />
                  HTTP {preview.data.meta.status}
                </span>
                <span className="tag">ISOLATED MOCK</span>
              </div>
              <pre className="json-result" aria-label="Preview response">
                {pretty(preview.data.data)}
              </pre>
              <ol className="timeline">
                {preview.data.timeline.map((stage, index) => (
                  <li key={index}>
                    <span>{stage.stage.replaceAll('_', ' ')}</span>
                    <span>
                      {stage.status}
                      {stage.elapsed_ms !== undefined ? ` · ${stage.elapsed_ms} ms` : ''}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </section>
      )}
    </>
  )
}

export default function Connectors({ role }: { role: Session['role'] }) {
  const cache = useQueryClient()
  const [selected, setSelected] = useState('')
  const [releaseId, setReleaseId] = useState('')
  const [creating, setCreating] = useState(false)
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const connectors = useQuery({
    queryKey: ['connectors'],
    queryFn: () => api<Connector[]>('/connectors'),
  })
  const releases = useQuery({
    queryKey: ['releases', selected],
    enabled: !!selected,
    queryFn: () => api<Release[]>(`/connectors/${selected}/releases`),
  })
  const current = releases.data?.find((item) => item.id === releaseId) ?? releases.data?.[0]
  const bundle = useQuery({
    queryKey: ['bundle', selected, current?.id],
    enabled: !!current,
    queryFn: () => api<Bundle>(`/connectors/${selected}/releases/${current!.id}`),
  })
  const create = useMutation({
    mutationFn: () =>
      api<Connector>('/connectors', { method: 'POST', body: pretty({ slug, name }) }),
    onSuccess: (value) => {
      setSelected(value.slug)
      setReleaseId('')
      setCreating(false)
      void cache.invalidateQueries({ queryKey: ['connectors'] })
    },
  })
  function saved(id: string) {
    setReleaseId(id)
    void cache.invalidateQueries({ queryKey: ['releases', selected] })
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">CONTRACTS INTO CONNECTIONS</div>
          <h1>Connectors</h1>
          <p>Import a contract, shape its responses, and verify the behavior.</p>
        </div>
        {role === 'admin' && (
          <button className="primary" onClick={() => setCreating((value) => !value)}>
            <Plus size={16} />
            New connector
          </button>
        )}
      </div>
      {creating && (
        <section className="panel">
          <h2>Create a connector</h2>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              create.mutate()
            }}
          >
            <div className="form-grid">
              <label>
                Connector identifier
                <input
                  required
                  pattern="[a-z][a-z0-9-]{0,47}"
                  value={slug}
                  onChange={(e) => setSlug(e.target.value)}
                  placeholder="inventory"
                />
              </label>
              <label>
                Display name
                <input
                  required
                  maxLength={100}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Inventory"
                />
              </label>
            </div>
            <button className="primary" disabled={create.isPending}>
              Create connector
            </button>
            {create.error && (
              <p role="alert" className="error">
                {create.error.message}
              </p>
            )}
          </form>
        </section>
      )}
      {connectors.isPending ? (
        <p role="status">Loading connectors…</p>
      ) : connectors.error ? (
        <p role="alert" className="error">
          {connectors.error.message}
        </p>
      ) : !connectors.data?.length ? (
        <section className="panel empty">
          <Boxes />
          <h2>No connectors yet</h2>
          <p>Create your first connector to open the contract workbench.</p>
        </section>
      ) : (
        <div className="connector-grid">
          {connectors.data.map((item) => (
            <button
              key={item.slug}
              className={`connector-card ${selected === item.slug ? 'selected' : ''}`}
              aria-pressed={selected === item.slug}
              onClick={() => {
                setSelected(item.slug)
                setReleaseId('')
              }}
            >
              <span className="section-icon">
                <Boxes size={20} />
              </span>
              <strong>{item.name}</strong>
              <code>{item.slug}</code>
              <span className="tag">{item.paused ? 'Paused' : 'Mock provider'}</span>
            </button>
          ))}
        </div>
      )}
      {selected && (
        <>
          <div className="section-heading">
            <h2>{connectors.data?.find((item) => item.slug === selected)?.name}</h2>
            {!!releases.data?.length && (
              <label>
                Saved release
                <select value={current?.id ?? ''} onChange={(e) => setReleaseId(e.target.value)}>
                  {releases.data.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.version} · {item.operations} operations
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {releases.isPending || (current && bundle.isPending) ? (
            <p role="status">Loading contract…</p>
          ) : releases.error || bundle.error ? (
            <p className="error" role="alert">
              {(releases.error ?? bundle.error)?.message}
            </p>
          ) : (
            <Editor
              key={`${selected}/${current?.id ?? 'new'}`}
              slug={selected}
              release={current}
              bundle={bundle.data}
              role={role}
              saved={saved}
            />
          )}
        </>
      )}
    </>
  )
}
