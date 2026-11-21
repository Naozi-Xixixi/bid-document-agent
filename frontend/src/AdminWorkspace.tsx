import { useEffect, useState, type FormEvent } from 'react'
import {
  createAdminCompany,
  createAdminUser,
  listAdminCompanies,
  listAdminUsers,
  updateAdminCompany,
  updateAdminUser,
  type AdminUser,
  type AuthCompany,
} from './auth'
import { useAuth } from './AuthWorkspace'

export function AdminWorkspace({ onHome }: { onHome: () => void }) {
  const { user, company, logout } = useAuth()
  const [companies, setCompanies] = useState<AuthCompany[]>([])
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [companyName, setCompanyName] = useState('')
  const [newUser, setNewUser] = useState({ username: '', password: '', display_name: '', company_id: '', role: 'company_user' })

  async function load() {
    setLoading(true)
    setError('')
    try {
      const [companyResult, userResult] = await Promise.all([listAdminCompanies(), listAdminUsers()])
      setCompanies(companyResult.data)
      setUsers(userResult.data)
      setNewUser(prev => ({ ...prev, company_id: prev.company_id || companyResult.data.find(item => item.status === 'active')?.company_id || '' }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '管理数据加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  async function run(action: () => Promise<unknown>, successMessage: string) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await action()
      setNotice(successMessage)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  function submitCompany(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = companyName.trim()
    if (!name) return setError('请输入公司名称')
    void run(async () => { await createAdminCompany(name); setCompanyName('') }, '公司已创建')
  }

  function submitUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!newUser.username.trim() || !newUser.password || !newUser.company_id) {
      setError('请填写账号、密码并选择所属公司')
      return
    }
    void run(async () => {
      await createAdminUser({ ...newUser, username: newUser.username.trim(), display_name: newUser.display_name.trim() })
      setNewUser(prev => ({ ...prev, username: '', password: '', display_name: '' }))
    }, '账号已创建')
  }

  async function resetPassword(item: AdminUser) {
    const password = window.prompt(`为账号 ${item.username} 设置新密码（至少 12 位）`)
    if (!password) return
    if (item.user_id !== user?.user_id) {
      void run(() => updateAdminUser(item.user_id, { password }), '密码已重置')
      return
    }
    setBusy(true)
    setError('')
    try {
      await updateAdminUser(item.user_id, { password })
      await logout()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '密码重置失败')
    } finally {
      setBusy(false)
    }
  }

  function updateUser(item: AdminUser, payload: Parameters<typeof updateAdminUser>[1], message: string) {
    void run(() => updateAdminUser(item.user_id, payload), message)
  }

  function changeCompanyStatus(item: AuthCompany) {
    const nextStatus = item.status === 'active' ? 'disabled' : 'active'
    void run(() => updateAdminCompany(item.company_id, nextStatus), '公司状态已更新')
  }

  const activeCompanies = companies.filter(item => item.status === 'active')

  return (
    <div className="admin-page">
      <header className="module-topbar">
        <div className="module-topbar-title"><button className="logo-button admin-logo" type="button" onClick={onHome}>木石科技</button><span>账号管理</span></div>
        <div className="admin-topbar-meta"><span>{company?.company_name || '示例公司'}</span><span>{user?.display_name || user?.username}</span><button className="light-btn" type="button" onClick={onHome}>返回首页</button></div>
      </header>
      <main className="module-page admin-content">
        <section className="module-header-card admin-heading"><div><p>ACCOUNT MANAGEMENT</p><h1>公司与账号</h1><span>为不同公司创建独立工作空间，并统一管理账号权限和状态。</span></div><button className="light-btn" type="button" onClick={() => void load()} disabled={loading || busy}>刷新</button></section>
        {error && <div className="admin-message admin-message-error">{error}</div>}
        {notice && <div className="admin-message admin-message-success">{notice}</div>}
        <div className="admin-grid">
          <section className="form-card admin-card">
            <div className="admin-card-head"><div><h2>公司</h2><span>公司之间的数据相互隔离</span></div><strong>{companies.length}</strong></div>
            <form className="admin-inline-form" onSubmit={submitCompany}><input value={companyName} onChange={event => setCompanyName(event.target.value)} placeholder="输入公司名称" /><button className="dark-btn" type="submit" disabled={busy}>新增公司</button></form>
            <div className="admin-company-list">
              {companies.map(item => <div className="admin-company-row" key={item.company_id}><div><strong>{item.company_name}</strong><span>{item.user_count || 0} 个账号 · {item.company_id === 'company_default' ? '默认公司' : '独立数据库'}</span></div><button className="light-btn" type="button" disabled={busy || item.company_id === 'company_default'} onClick={() => changeCompanyStatus(item)}>{item.status === 'active' ? '停用' : '启用'}</button></div>)}
            </div>
          </section>
          <section className="form-card admin-card">
            <div className="admin-card-head"><div><h2>新增账号</h2><span>账号登录后只访问所属公司的数据</span></div></div>
            <form className="admin-user-form" onSubmit={submitUser}>
              <label>账号<input value={newUser.username} onChange={event => setNewUser(prev => ({ ...prev, username: event.target.value }))} placeholder="例如：project_user" /></label>
              <label>初始密码<input type="password" value={newUser.password} onChange={event => setNewUser(prev => ({ ...prev, password: event.target.value }))} placeholder="至少 12 位" /></label>
              <label>显示名称<input value={newUser.display_name} onChange={event => setNewUser(prev => ({ ...prev, display_name: event.target.value }))} placeholder="可选" /></label>
              <label>所属公司<select value={newUser.company_id} onChange={event => setNewUser(prev => ({ ...prev, company_id: event.target.value }))}><option value="">请选择公司</option>{activeCompanies.map(item => <option key={item.company_id} value={item.company_id}>{item.company_name}</option>)}</select></label>
              <label>账号角色<select value={newUser.role} onChange={event => setNewUser(prev => ({ ...prev, role: event.target.value }))}><option value="company_user">公司用户</option><option value="admin">管理员</option></select></label>
              <button className="dark-btn" type="submit" disabled={busy || !activeCompanies.length}>创建账号</button>
            </form>
          </section>
        </div>
        <section className="form-card admin-card admin-users-card">
          <div className="admin-card-head"><div><h2>账号列表</h2><span>可以修改所属公司、角色、状态或重置密码</span></div><strong>{users.length}</strong></div>
          {loading ? <div className="admin-empty">正在加载账号…</div> : <div className="admin-user-table-wrap"><table className="admin-user-table"><thead><tr><th>账号</th><th>所属公司</th><th>角色</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead><tbody>{users.map(item => <tr key={item.user_id}><td><strong>{item.display_name}</strong><span>{item.username}</span></td><td><select value={item.company_id} disabled={busy || item.user_id === user?.user_id} onChange={event => updateUser(item, { company_id: event.target.value }, '所属公司已更新')}>{companies.filter(companyItem => companyItem.status === 'active').map(companyItem => <option key={companyItem.company_id} value={companyItem.company_id}>{companyItem.company_name}</option>)}</select></td><td><select value={item.role} disabled={busy || item.user_id === user?.user_id} onChange={event => updateUser(item, { role: event.target.value }, '账号角色已更新')}><option value="company_user">公司用户</option><option value="admin">管理员</option></select></td><td><span className={`admin-status ${item.status}`}>{item.status === 'active' ? '启用' : '停用'}</span></td><td>{formatDate(item.last_login_at)}</td><td><div className="admin-row-actions"><button className="light-btn" type="button" disabled={busy || item.user_id === user?.user_id} onClick={() => updateUser(item, { status: item.status === 'active' ? 'disabled' : 'active' }, '账号状态已更新')}>{item.status === 'active' ? '停用' : '启用'}</button><button className="light-btn" type="button" disabled={busy} onClick={() => resetPassword(item)}>重置密码</button></div></td></tr>)}</tbody></table></div>}
        </section>
      </main>
    </div>
  )
}

function formatDate(value?: string | null) {
  return value ? value.replace('T', ' ').slice(0, 16) : '—'
}
