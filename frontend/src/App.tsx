import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import type { ComponentType, FormEvent, ReactNode } from 'react'
import './App.css'

// Plotly rất nặng (~4MB) nên chỉ tải khi có biểu đồ đầu tiên
const Plot = lazy(async () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const m: any = await import('react-plotly.js')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return { default: (m.default?.default ?? m.default) as ComponentType<any> }
})

type Row = Record<string, unknown>
type Mode = 'natural' | 'chart' | 'sql'
type Step = { step: number; description: string; status: string; result: string }

type Result = {
  sql?: string
  rows?: Row[]
  used_sample?: boolean
  chart_json?: string | null
  workflow?: Step[]
  open?: boolean // mở sẵn SQL + bảng (dùng cho chế độ SQL)
}

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  code?: boolean
  result?: Result
}

// Lịch sử khi chưa đăng nhập, lưu trong localStorage
type LocalSession = {
  sessionId: string
  title: string
  messages: { question: string; answer: string; sql: string; rows: Row[]; workflow?: Step[] }[]
  createdAt: string
  updatedAt: string
}

type ServerMessage = {
  id: number
  role: 'user' | 'assistant'
  content: string
  sql?: string | null
  used_sample?: boolean
  rows?: Row[] | null
  metadata?: { workflow?: Step[]; chart_json?: string | null } | null
}

const HISTORY_KEY = 'afs:history'

const MODES: Record<Mode, { label: string; placeholder: string; loading: string }> = {
  natural: {
    label: 'Hỏi đáp',
    placeholder: 'Hỏi về giá, khối lượng, công ty DJIA… (VI/EN)',
    loading: 'Agent đang phân tích câu hỏi, sinh SQL và truy vấn dữ liệu…',
  },
  chart: {
    label: 'Biểu đồ',
    placeholder: 'Mô tả biểu đồ cần vẽ, ví dụ: giá đóng cửa của Apple từ 01/06/2025 đến 30/09/2025',
    loading: 'Đang truy vấn dữ liệu và vẽ biểu đồ…',
  },
  sql: {
    label: 'SQL',
    placeholder: 'SELECT … FROM prices | companies',
    loading: 'Đang thực thi SQL…',
  },
}

const SUGGESTIONS: { mode: Mode; text: string }[] = [
  { mode: 'natural', text: 'What was the closing price of Microsoft on March 15, 2024?' },
  { mode: 'natural', text: 'Giá đóng cửa trung bình của Apple trong năm 2025 là bao nhiêu?' },
  { mode: 'chart', text: 'Plot the closing price of Nvidia from January 1, 2025 to June 30, 2025' },
  { mode: 'natural', text: 'What is the DJIA and how is it calculated?' },
  { mode: 'sql', text: 'SELECT symbol, name, sector, market_cap FROM companies ORDER BY market_cap DESC LIMIT 10' },
]

const ICONS = {
  menu: 'M4 6h16M4 12h16M4 18h16',
  plus: 'M12 5v14M5 12h14',
  send: 'M12 19V5M5 12l7-7 7 7',
  trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  chart: 'M4 19h16M7 16V9M12 16V5M17 16v-4',
}

function Icon({ name }: { name: keyof typeof ICONS }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICONS[name]} />
    </svg>
  )
}

async function api<T>(path: string, body?: unknown, method = body ? 'POST' : 'GET'): Promise<T> {
  const res = await fetch(`/api/${path}`, {
    method,
    credentials: 'include',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || data.detail || `HTTP ${res.status}`)
  return data as T
}

function loadHistory(): LocalSession[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(HISTORY_KEY) || '[]')
    return Array.isArray(parsed) ? parsed.filter((s) => s?.sessionId) : []
  } catch {
    return []
  }
}

function Section({ title, open, badge, children }: { title: string; open?: boolean; badge?: ReactNode; children: ReactNode }) {
  return (
    <details className="result" open={open}>
      <summary>
        <span>{title}</span>
        {badge}
      </summary>
      {children}
    </details>
  )
}

function DataTable({ rows }: { rows: Row[] }) {
  const columns = Object.keys(rows[0])
  return (
    <div className="table-wrapper">
      <table>
        <thead>
          <tr>{columns.map((col) => <th key={col}>{col}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((col) => {
                const v = row[col]
                return typeof v === 'number'
                  ? <td key={col} className="num">{v.toLocaleString('en-US', { maximumFractionDigits: 4 })}</td>
                  : <td key={col}>{String(v ?? '')}</td>
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Chart({ json }: { json: string }) {
  let fig
  try {
    fig = JSON.parse(json)
  } catch {
    return <p className="hint">Không thể hiển thị biểu đồ.</p>
  }
  // Lấy màu từ theme hiện tại để biểu đồ đọc được ở cả light và dark mode
  const css = getComputedStyle(document.documentElement)
  const axis = (a: object | undefined) => ({ ...a, gridcolor: css.getPropertyValue('--line'), zerolinecolor: css.getPropertyValue('--line') })
  const layout = {
    ...fig.layout,
    template: undefined,
    autosize: true,
    width: undefined,
    height: 420,
    margin: { l: 60, r: 24, t: fig.layout?.title ? 56 : 24, b: 60 },
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    font: { ...fig.layout?.font, color: css.getPropertyValue('--ink') },
    xaxis: axis(fig.layout?.xaxis),
    yaxis: axis(fig.layout?.yaxis),
  }
  return (
    <Suspense fallback={<p className="hint">Đang tải biểu đồ…</p>}>
      <Plot
        data={fig.data}
        layout={layout}
        config={{ responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'] }}
        useResizeHandler
        style={{ width: '100%', height: 420 }}
      />
    </Suspense>
  )
}

function AssistantResult({ result }: { result: Result }) {
  const { sql, rows = [], chart_json, workflow = [], used_sample, open } = result
  return (
    <>
      {workflow.length > 0 && (
        <Section title={`${workflow.length} bước xử lý của agent`}>
          <ol className="steps">
            {workflow.map((s) => (
              <li key={s.step} className={s.status}>
                <strong>{s.description}</strong>
                <span>{s.result}</span>
              </li>
            ))}
          </ol>
        </Section>
      )}
      {sql && (
        <Section title="SQL đã chạy" open={open} badge={used_sample && <span className="badge">SQL mẫu</span>}>
          <pre className="code">{sql}</pre>
        </Section>
      )}
      {rows.length > 0 && (
        <Section title={`Bảng dữ liệu · ${rows.length} dòng`} open={open}>
          <DataTable rows={rows} />
        </Section>
      )}
      {chart_json && (
        <Section title="Biểu đồ" open>
          <Chart json={chart_json} />
        </Section>
      )}
    </>
  )
}

function App() {
  const [input, setInput] = useState('')
  const [mode, setMode] = useState<Mode>('natural')
  const [loading, setLoading] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [history, setHistory] = useState<LocalSession[]>(loadHistory)
  const [conversations, setConversations] = useState<{ id: string; title: string }[]>([])
  // id phiên đang mở: conversation trên server (đã đăng nhập) hoặc session cục bộ
  const [activeId, setActiveId] = useState<string | null>(null)
  const [user, setUser] = useState<{ username: string } | null>(null)
  const [page, setPage] = useState<'chat' | 'auth'>('chat')
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login')
  const [authForm, setAuthForm] = useState({ username: '', password: '' })
  const [authNotice, setAuthNotice] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 768)
  const endRef = useRef<HTMLDivElement>(null)

  const fetchConversations = useCallback(() => {
    api<{ id: string; title: string }[]>('conversations/').then(setConversations).catch(console.error)
  }, [])

  useEffect(() => {
    api<{ username: string }>('auth/me/').then(setUser).catch(() => {})
  }, [])

  useEffect(() => {
    if (user) fetchConversations()
    else setConversations([])
  }, [user, fetchConversations])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, loading])

  const saveHistory = (next: LocalSession[]) => {
    setHistory(next)
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(next))
  }

  const newChat = () => {
    setActiveId(null)
    setMessages([])
    setInput('')
    setPage('chat')
  }

  const handleAuth = async (e: FormEvent) => {
    e.preventDefault()
    setAuthNotice(null)
    if (!authForm.username || !authForm.password) {
      setAuthNotice('Vui lòng nhập username và password.')
      return
    }
    try {
      if (authMode === 'register') {
        await api('auth/register/', authForm)
        setAuthMode('login')
        setAuthForm({ username: authForm.username, password: '' })
        setAuthNotice('Đăng ký thành công, hãy đăng nhập.')
      } else {
        setUser(await api('auth/login/', authForm))
        setAuthForm({ username: '', password: '' })
        newChat()
      }
    } catch (err) {
      setAuthNotice((err as Error).message)
    }
  }

  const handleLogout = async () => {
    await api('auth/logout/', {}).catch(console.error)
    setUser(null)
    newChat()
  }

  const openSession = async (id: string) => {
    setPage('chat')
    if (window.innerWidth <= 768) setSidebarOpen(false)
    if (!user) {
      const session = history.find((s) => s.sessionId === id)
      if (!session) return
      setMessages(
        session.messages.flatMap((m, i) => [
          { id: `u-${i}`, role: 'user' as const, content: m.question },
          { id: `a-${i}`, role: 'assistant' as const, content: m.answer, result: { sql: m.sql, rows: m.rows, workflow: m.workflow } },
        ]),
      )
      setActiveId(id)
      return
    }
    try {
      const data = await api<{ messages: ServerMessage[] }>(`conversations/${id}/messages/`)
      setMessages(
        data.messages.map((m) => ({
          id: String(m.id),
          role: m.role,
          content: m.content,
          result: m.role === 'assistant'
            ? { sql: m.sql || undefined, rows: m.rows || undefined, used_sample: m.used_sample, chart_json: m.metadata?.chart_json, workflow: m.metadata?.workflow }
            : undefined,
        })),
      )
      setActiveId(id)
    } catch (err) {
      console.error(err)
    }
  }

  const deleteSession = async (id: string) => {
    if (!window.confirm('Bạn có chắc chắn muốn xóa phiên chat này?')) return
    if (user) {
      await api(`conversations/${id}/`, undefined, 'DELETE').catch(console.error)
      fetchConversations()
    } else {
      saveHistory(history.filter((s) => s.sessionId !== id))
    }
    if (activeId === id) newChat()
  }

  const handleSend = async (e?: FormEvent) => {
    e?.preventDefault()
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    setLoading(true)
    const stamp = Date.now()
    setMessages((prev) => [...prev, { id: `u-${stamp}`, role: 'user', content: text, code: mode === 'sql' }])

    try {
      let reply: ChatMessage
      if (mode === 'sql') {
        // SQL trực tiếp: không qua agent, không lưu vào lịch sử
        const data = await api<{ sql: string; rows: Row[] }>('execute-sql/', { sql: text })
        reply = {
          id: `a-${stamp}`,
          role: 'assistant',
          content: `Đã thực thi SQL thành công. Trả về ${data.rows.length} dòng dữ liệu.`,
          result: { sql: data.sql, rows: data.rows, open: true },
        }
      } else {
        const data = await api<Result & { answer: string; sql: string; rows: Row[]; conversation_id: string | null }>('query/', {
          question: text,
          conversation_id: user && activeId ? activeId : undefined,
          force_chart: mode === 'chart',
        })
        reply = { id: `a-${stamp}`, role: 'assistant', content: data.answer, result: data }

        if (user) {
          setActiveId(data.conversation_id)
          fetchConversations()
        } else {
          const now = new Date().toISOString()
          const turn = { question: text, answer: data.answer, sql: data.sql, rows: data.rows, workflow: data.workflow }
          const sessionId = activeId ?? `local-${stamp}`
          saveHistory(
            activeId
              ? history.map((s) => (s.sessionId === activeId ? { ...s, messages: [...s.messages, turn], updatedAt: now } : s))
              : [...history, { sessionId, title: text, messages: [turn], createdAt: now, updatedAt: now }],
          )
          setActiveId(sessionId)
        }
      }
      setMessages((prev) => [...prev, reply])
    } catch (err) {
      setMessages((prev) => [...prev, { id: `e-${stamp}`, role: 'assistant', content: `Đã xảy ra lỗi: ${(err as Error).message}` }])
    } finally {
      setLoading(false)
    }
  }

  const sessions = user
    ? conversations
    : [...history].reverse().map((s) => ({ id: s.sessionId, title: s.title }))

  const composer = (
    <form className="composer" onSubmit={handleSend}>
      <textarea
        className={mode === 'sql' ? 'mono' : undefined}
        placeholder={MODES[mode].placeholder}
        value={input}
        rows={1}
        autoFocus
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          // Enter để gửi, Shift+Enter để xuống dòng
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) handleSend(e)
        }}
      />
      <div className="composer-bar">
        <div className="segmented" role="radiogroup" aria-label="Chế độ">
          {(Object.keys(MODES) as Mode[]).map((m) => (
            <button key={m} type="button" role="radio" aria-checked={mode === m} className={mode === m ? 'active' : ''} onClick={() => setMode(m)}>
              {MODES[m].label}
            </button>
          ))}
        </div>
        <button className="send" type="submit" disabled={loading || !input.trim()} title={mode === 'sql' ? 'Chạy SQL' : 'Gửi'} aria-label="Gửi">
          <Icon name="send" />
        </button>
      </div>
    </form>
  )

  return (
    <div className={`app ${sidebarOpen ? '' : 'sidebar-closed'}`}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><Icon name="chart" /></span>
          DJIA Agent
        </div>
        <button type="button" className="new-chat" onClick={newChat}>
          <Icon name="plus" /> Đoạn chat mới
        </button>

        <nav className="sessions">
          <h3>Gần đây</h3>
          {sessions.length === 0 && <p className="hint">Chưa có phiên chat nào.</p>}
          {sessions.map((s) => (
            <div key={s.id} className={`session ${activeId === s.id ? 'active' : ''}`}>
              <button type="button" onClick={() => openSession(s.id)}>{s.title || 'Phiên không tiêu đề'}</button>
              <button type="button" className="icon-button" title="Xóa" aria-label="Xóa phiên chat" onClick={() => deleteSession(s.id)}>
                <Icon name="trash" />
              </button>
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          {user ? (
            <>
              <span className="avatar">{user.username.charAt(0).toUpperCase()}</span>
              <span className="username">{user.username}</span>
              <button type="button" className="icon-button" title="Đăng xuất" aria-label="Đăng xuất" onClick={handleLogout}>
                <Icon name="logout" />
              </button>
            </>
          ) : (
            <>
              <p className="hint">Lịch sử đang lưu trên trình duyệt này.</p>
              <button type="button" className="button" onClick={() => { setPage('auth'); setAuthNotice(null) }}>
                Đăng nhập / Đăng ký
              </button>
            </>
          )}
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button type="button" className="icon-button" title="Ẩn/hiện sidebar" aria-label="Ẩn/hiện sidebar" onClick={() => setSidebarOpen(!sidebarOpen)}>
            <Icon name="menu" />
          </button>
          <h1>Multi-Agent Financial Information System</h1>
        </header>

        {page === 'auth' ? (
          <main className="center">
            <form className="auth-card" onSubmit={handleAuth}>
              <h2>{authMode === 'login' ? 'Đăng nhập' : 'Tạo tài khoản'}</h2>
              <p className="hint">Đăng nhập để lưu lịch sử phiên chat trên máy chủ.</p>
              <div className="segmented">
                {(['login', 'register'] as const).map((m) => (
                  <button key={m} type="button" className={authMode === m ? 'active' : ''} onClick={() => { setAuthMode(m); setAuthNotice(null) }}>
                    {m === 'login' ? 'Đăng nhập' : 'Đăng ký'}
                  </button>
                ))}
              </div>
              <label>
                Username
                <input type="text" autoComplete="username" autoFocus value={authForm.username} onChange={(e) => setAuthForm({ ...authForm, username: e.target.value })} />
              </label>
              <label>
                Password
                <input type="password" autoComplete={authMode === 'login' ? 'current-password' : 'new-password'} value={authForm.password} onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })} />
              </label>
              {authNotice && <p className="notice" role="alert">{authNotice}</p>}
              <button className="button primary" type="submit">{authMode === 'login' ? 'Đăng nhập' : 'Đăng ký'}</button>
              <button className="button ghost" type="button" onClick={() => setPage('chat')}>Quay lại chat</button>
            </form>
          </main>
        ) : messages.length === 0 && !loading ? (
          <main className="center">
            <div className="welcome">
              <h2>Bạn muốn biết gì về DJIA?</h2>
              <p className="hint">Hỏi bằng tiếng Việt hoặc tiếng Anh — agent sẽ sinh SQL, truy vấn dữ liệu và trả lời.</p>
              {composer}
              <div className="suggestions">
                <h3>Gợi ý</h3>
                {SUGGESTIONS.map((s) => (
                  <button key={s.text} type="button" onClick={() => { setMode(s.mode); setInput(s.text) }}>
                    <span className="tag">{MODES[s.mode].label}</span>
                    {s.text}
                  </button>
                ))}
              </div>
              <details className="about">
                <summary>Về dữ liệu và cách sử dụng</summary>
                <p><strong>30 công ty DJIA:</strong> Apple (AAPL), Amgen (AMGN), Amazon (AMZN), American Express (AXP), Boeing (BA), Caterpillar (CAT), Salesforce (CRM), Cisco (CSCO), Chevron (CVX), Disney (DIS), Goldman Sachs (GS), Home Depot (HD), Honeywell (HON), IBM (IBM), Johnson &amp; Johnson (JNJ), JPMorgan (JPM), Coca-Cola (KO), McDonald's (MCD), 3M (MMM), Merck (MRK), Microsoft (MSFT), Nike (NKE), Nvidia (NVDA), Procter &amp; Gamble (PG), Sherwin-Williams (SHW), Travelers (TRV), UnitedHealth (UNH), Visa (V), Verizon (VZ), Walmart (WMT).</p>
                <p><strong>Bảng companies:</strong> symbol, name, sector, industry, country, website, market_cap, pe_ratio, dividend_yield, week_52_high, week_52_low, description.</p>
                <p><strong>Bảng prices:</strong> date, open, high, low, close, volume, dividends, stock_splits, ticker — dữ liệu theo ngày từ 01/2023 đến nay.</p>
                <p><strong>Hỏi đáp:</strong> đặt câu hỏi tự nhiên; thêm từ "plot" hoặc "vẽ" để kèm biểu đồ. <strong>Biểu đồ:</strong> luôn vẽ biểu đồ cho yêu cầu. <strong>SQL:</strong> chạy thẳng câu lệnh SELECT trên database.</p>
              </details>
            </div>
          </main>
        ) : (
          <>
            <main className="thread">
              <div className="column">
                {messages.map((msg) =>
                  msg.role === 'user' ? (
                    <div key={msg.id} className="bubble">
                      {msg.code ? <pre className="code">{msg.content}</pre> : msg.content}
                    </div>
                  ) : (
                    <article key={msg.id} className="answer">
                      <p>{msg.content}</p>
                      {msg.result && <AssistantResult result={msg.result} />}
                    </article>
                  ),
                )}
                {loading && (
                  <div className="loading" role="status">
                    <span className="spinner" /> {MODES[mode].loading}
                  </div>
                )}
                <div ref={endRef} />
              </div>
            </main>
            <footer className="dock">
              <div className="column">{composer}</div>
            </footer>
          </>
        )}
      </div>
    </div>
  )
}

export default App
