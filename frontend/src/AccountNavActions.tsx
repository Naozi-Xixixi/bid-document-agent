import { useEffect, useState } from 'react'
import { meRequest } from './auth'

export function AccountNavActions({ onHome }: { onHome: () => void }) {
  const [accountName, setAccountName] = useState('正在获取账号…')
  useEffect(() => {
    let active = true
    void meRequest().then(({ data }) => {
      if (active) setAccountName(data.user.display_name || data.user.username)
    }).catch(() => { if (active) setAccountName('账号信息不可用') })
    return () => { active = false }
  }, [])
  return <nav className="nav-actions" aria-label="主导航"><span className="nav-company-name">当前账号：{accountName}</span><button className="nav-btn nav-btn-a" type="button" onClick={onHome}>返回首页</button></nav>
}
