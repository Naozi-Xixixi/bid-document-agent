import { createContext, useContext, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { clearAuthToken, loginRequest, logoutRequest, meRequest, setActiveCompany, type AuthCompany, type AuthUser } from './auth'

type AuthContextValue = {
  ready: boolean
  user: AuthUser | null
  company: AuthCompany | null
  login: (username: string, password: string) => Promise<string | null>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

function readableError(error: unknown) {
  return error instanceof Error ? error.message : '登录失败，请检查账号和密码'
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false)
  const [user, setUser] = useState<AuthUser | null>(null)
  const [company, setCompany] = useState<AuthCompany | null>(null)

  useEffect(() => {
    meRequest().then(result => {
      setUser(result.data.user)
      setCompany(result.data.company)
      setActiveCompany(result.data.company.company_id)
    }).catch(() => {
      // Keep the token if the API is temporarily unavailable. A successful
      // login replaces it; logout still clears it explicitly.
      setUser(null)
      setCompany(null)
    }).finally(() => setReady(true))
  }, [])

  const value = useMemo<AuthContextValue>(() => ({
    ready,
    user,
    company,
    login: async (username, password) => {
      try {
        const session = await loginRequest(username, password)
        setUser(session.user)
        setCompany(session.company)
        return null
      } catch (error) {
        return readableError(error)
      }
    },
    logout: async () => {
      try {
        await logoutRequest()
      } catch {
        clearAuthToken()
      } finally {
        setUser(null)
        setCompany(null)
      }
    },
  }), [company, ready, user])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth 必须在 AuthProvider 内使用')
  return context
}

export function AuthGate({ children }: { children: ReactNode }) {
  const { ready, user } = useAuth()
  if (!ready) return <main className="auth-page"><div className="auth-panel"><p className="auth-eyebrow">示例公司智能标书助手</p><strong>正在检查登录状态</strong></div></main>
  if (!user) return <LoginPage />
  return <>{children}</>
}

function LoginPage() {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!username.trim() || !password) {
      setError('请输入账号和密码')
      return
    }
    setError('')
    setSubmitting(true)
    const message = await login(username.trim(), password)
    if (message) setError(message)
    setSubmitting(false)
  }

  return (
    <main className="auth-page">
      <section className="auth-panel" aria-labelledby="login-title">
        <p className="auth-eyebrow">示例公司智能标书助手</p>
        <h1 id="login-title">登录</h1>
        <p className="auth-subtitle">登录后进入所属公司的工作空间</p>
        <form className="auth-form" onSubmit={handleSubmit}>
          <label>账号<input autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} placeholder="请输入账号" /></label>
          <label>密码<input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} placeholder="请输入密码" /></label>
          {error && <div className="auth-error" role="alert">{error}</div>}
          <button className="dark-btn auth-submit" type="submit" disabled={submitting}>{submitting ? '登录中…' : '登录'}</button>
        </form>
      </section>
    </main>
  )
}
