import React, { useState } from 'react';
import { ArrowLeft, LockKeyhole, Mail, UserRound } from 'lucide-react';
import { ApiError, authApi } from './api/client';

function Field({ icon: Icon, label, ...props }) {
  return <label className="authField"><span><Icon size={17}/>{label}</span><input {...props}/></label>;
}

export function AuthScreen({ mode = 'login', navigate }) {
  const isLogin = mode === 'login';
  const [form, setForm] = useState({ email: '', password: '', displayName: '' });
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const update = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }));
  async function submit(event) {
    event.preventDefault();
    setPending(true); setMessage('');
    try {
      if (isLogin) {
        const response = await authApi.login({ email: form.email, password: form.password });
        const role = response.data.user.role;
        navigate(['admin', 'editor', 'author'].includes(role) ? '/studio' : '/');
      } else {
        await authApi.register({ email: form.email, password: form.password, displayName: form.displayName });
        setMessage('注册请求已提交，请查收邮箱验证邮件后登录。');
      }
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : '网络连接失败，请稍后重试。');
    } finally { setPending(false); }
  }
  return <main className="authPage"><button className="backHome" onClick={() => navigate('/')}><ArrowLeft size={17}/>返回首页</button><section className="authCard"><div className="authMark">猫</div><p className="authEyebrow">猫子的世界</p><h1>{isLogin ? '登录你的账号' : '创建账号'}</h1><p>{isLogin ? '登录后可评论、管理账户与进入授权的工作台。' : '注册后需要完成邮箱验证，才可以发表评论。'}</p><form onSubmit={submit}>{!isLogin && <Field icon={UserRound} label="显示名称" value={form.displayName} onChange={update('displayName')} required minLength="1" maxLength="120"/>}<Field icon={Mail} label="邮箱" type="email" value={form.email} onChange={update('email')} required/><Field icon={LockKeyhole} label="密码" type="password" value={form.password} onChange={update('password')} required minLength={isLogin ? 1 : 15} autoComplete={isLogin ? 'current-password' : 'new-password'}/>{message && <p className="authMessage" role="status">{message}</p>}<button className="authSubmit" disabled={pending}>{pending ? '处理中…' : isLogin ? '登录' : '注册并发送验证邮件'}</button></form><button className="authSwitch" onClick={() => navigate(isLogin ? '/register' : '/login')}>{isLogin ? '还没有账号？创建账号' : '已有账号？返回登录'}</button></section></main>;
}
