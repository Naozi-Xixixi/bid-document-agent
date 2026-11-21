const LEGACY_AUTH_TOKEN_KEY = 'new_biaoshuai.accessToken'
const localApiBase = typeof window === 'undefined'
  ? 'http://127.0.0.1:3896/api'
  : `${window.location.protocol}//${window.location.hostname}:3896/api`
export const API_BASE = import.meta.env.VITE_API_BASE ?? localApiBase
const ACTIVE_COMPANY_KEY = 'new_biaoshuai.activeCompany'

export type AuthUser = {
  user_id: number
  username: string
  display_name: string
  role: 'admin' | 'company_user' | string
  company_id: string
  status: 'active' | 'disabled' | string
  last_login_at?: string | null
  create_time: string
  update_time: string
}

export type AuthCompany = {
  company_id: string
  company_name: string
  status: 'active' | 'disabled' | string
  user_count?: number
  create_time: string
  update_time: string
}

export type AuthIdentity = { user: AuthUser; company: AuthCompany }
export type AuthSession = AuthIdentity & { access_token: string; expires_at: string }

export type AuthApiEnvelope<T> = { code: number; message: string; data: T }

export function clearAuthToken() {
  if (typeof window === 'undefined') return
  window.localStorage.removeItem(LEGACY_AUTH_TOKEN_KEY)
  window.localStorage.removeItem(ACTIVE_COMPANY_KEY)
}

export function getAuthToken() {
  if (typeof window === 'undefined') return null
  return window.localStorage.getItem(LEGACY_AUTH_TOKEN_KEY)
}

export function setAuthToken(token: string) {
  if (typeof window === 'undefined' || !token) return
  window.localStorage.setItem(LEGACY_AUTH_TOKEN_KEY, token)
}

export function setActiveCompany(companyId: string) {
  window.localStorage.setItem(ACTIVE_COMPANY_KEY, companyId)
}

export function companyStorageKey(baseKey: string) {
  const companyId = typeof window === 'undefined' ? '' : window.localStorage.getItem(ACTIVE_COMPANY_KEY) || ''
  if (!companyId || companyId === 'company_default') return baseKey
  return `${baseKey}:${companyId}`
}

function errorMessage(raw: string, fallback: string) {
  try {
    const parsed = JSON.parse(raw) as { detail?: string; message?: string }
    return parsed.detail || parsed.message || fallback
  } catch {
    return raw || fallback
  }
}

export async function authRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  const token = getAuthToken()
  if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`)
  if (!(options.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: 'include' })
  const text = await response.text()
  if (!response.ok) throw new Error(errorMessage(text, `请求失败（${response.status}）`))
  try {
    return JSON.parse(text) as T
  } catch {
    throw new Error('服务器返回了无法识别的数据')
  }
}

export async function loginRequest(username: string, password: string) {
  const result = await authRequest<AuthApiEnvelope<AuthSession>>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
  setAuthToken(result.data.access_token)
  setActiveCompany(result.data.company.company_id)
  return result.data
}

export function meRequest() {
  return authRequest<AuthApiEnvelope<AuthIdentity>>('/auth/me')
}

export function logoutRequest() {
  return authRequest<AuthApiEnvelope<null>>('/auth/logout', { method: 'POST' }).finally(clearAuthToken)
}

export function listAdminCompanies() {
  return authRequest<AuthApiEnvelope<AuthCompany[]>>('/auth/admin/companies')
}

export function createAdminCompany(company_name: string) {
  return authRequest<AuthApiEnvelope<AuthCompany>>('/auth/admin/companies', {
    method: 'POST',
    body: JSON.stringify({ company_name }),
  })
}

export function updateAdminCompany(companyId: string, status: 'active' | 'disabled') {
  return authRequest<AuthApiEnvelope<AuthCompany>>(`/auth/admin/companies/${encodeURIComponent(companyId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export type AdminUser = AuthUser & { company_name: string }

export function listAdminUsers() {
  return authRequest<AuthApiEnvelope<AdminUser[]>>('/auth/admin/users')
}

export function createAdminUser(payload: { username: string; password: string; display_name: string; company_id: string; role: string }) {
  return authRequest<AuthApiEnvelope<AdminUser>>('/auth/admin/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateAdminUser(userId: number, payload: { status?: string; password?: string; display_name?: string; company_id?: string; role?: string }) {
  return authRequest<AuthApiEnvelope<AdminUser>>(`/auth/admin/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}
