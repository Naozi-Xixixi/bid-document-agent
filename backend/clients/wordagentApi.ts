const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:3890/api'

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(options.headers ?? {}),
    },
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<T>
}

export type BidSettings = {
  bidTitle: string
  pageCount: number
  bidType: 'open' | 'blind'
  selectedModel: string
  selectedProvider: string
}

export const wordagentApi = {
  health: () => request('/health'),
  models: () => request('/models'),
  settings: () => request('/settings'),
  createProject: (settings: BidSettings) => request('/projects', {
    method: 'POST',
    body: JSON.stringify({ settings }),
  }),
  listProjects: () => request('/projects'),
  getProject: (projectId: string) => request(`/projects/${projectId}`),
  uploadFiles: (projectId: string, files: File[]) => {
    const form = new FormData()
    files.forEach(file => form.append('files', file))
    return request(`/projects/${projectId}/files`, { method: 'POST', body: form })
  },
  parseTender: (projectId: string, pastedText = '') => request(`/projects/${projectId}/tender/parse`, {
    method: 'POST',
    body: JSON.stringify({ method: pastedText ? 'manual' : 'auto', pastedText }),
  }),
  generateDirectory: (projectId: string, manualDirectory = '') => request(`/projects/${projectId}/directory/generate`, {
    method: 'POST',
    body: JSON.stringify({ strategy: manualDirectory ? 'manual' : 'score_points', manualDirectory }),
  }),
  expandDirectory: (projectId: string, instruction = '') => request(`/projects/${projectId}/directory/expand`, {
    method: 'POST',
    body: JSON.stringify({ instruction }),
  }),
  optimizeDirectory: (projectId: string, instruction = '') => request(`/projects/${projectId}/directory/optimize`, {
    method: 'POST',
    body: JSON.stringify({ instruction }),
  }),
  exportDirectoryUrl: (projectId: string) => `${API_BASE}/projects/${projectId}/directory/export`,
  generateSection: (projectId: string, payload: { sectionId: string; title: string; subItems?: string[]; wordTarget?: number }) =>
    request(`/projects/${projectId}/sections/generate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  generateSectionById: (projectId: string, sectionId: string, payload: { title?: string; subItems?: string[]; requirements?: string[]; wordTarget?: number } = {}) =>
    request(`/projects/${projectId}/sections/${sectionId}/generate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  generateNextSection: (projectId: string, payload: { wordTarget?: number } = {}) =>
    request(`/projects/${projectId}/sections/next/generate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getSection: (projectId: string, sectionId: string) => request(`/projects/${projectId}/sections/${sectionId}`),
  optimizeSection: (projectId: string, sectionId: string, instruction = '') =>
    request(`/projects/${projectId}/sections/${sectionId}/optimize`, {
      method: 'POST',
      body: JSON.stringify({ instruction }),
    }),
  generateSectionPpt: (projectId: string, sectionId: string) =>
    request(`/projects/${projectId}/sections/${sectionId}/ppt`, { method: 'POST', body: JSON.stringify({}) }),
  chat: (message: string, projectId?: string) => request('/agent/chat', {
    method: 'POST',
    body: JSON.stringify({ message, projectId }),
  }),
  exportDocxUrl: (projectId: string) => `${API_BASE}/projects/${projectId}/export/docx`,
}
