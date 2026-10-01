import React, { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, LockKeyhole, Mail, ShieldCheck, UserRound } from 'lucide-react';
import { ApiError, authApi } from './api/client';
import brandLogo from './assets/cats-world-logo-ocean.png';

function Field({ icon: Icon, label, ...props }) {
  return <label className="authField"><span><Icon size={17}/>{label}</span><input {...props}/></label>;
}

export function AuthScreen({ mode = 'login', navigate, location = window.location.pathname + window.location.search }) {
  const isLogin = mode === 'login';
  const returnTo = (() => {
    const query = new URLSearchParams(location.split('?')[1] || '');
    const candidate = query.get('returnTo') || query.get('next');
    return candidate && candidate.startsWith('/') && !candidate.startsWith('//') ? candidate : null;
  })();
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
        navigate(['admin', 'editor', 'author'].includes(role) ? (returnTo || '/studio/home') : '/');
      } else {
        await authApi.register({ email: form.email, password: form.password, displayName: form.displayName });
        setMessage('注册请求已提交，请查收邮箱验证邮件后登录。');
      }
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : '网络连接失败，请稍后重试。');
    } finally { setPending(false); }
  }
  return <main className="authPage"><div className="authGlow authGlowOne"/><div className="authGlow authGlowTwo"/><button className="backHome" onClick={() => navigate('/')}><ArrowLeft size={17}/>返回首页</button><div className="authLayout"><aside className="authIntro"><a className="authBrand" href="/" onClick={(event) => { event.preventDefault(); navigate('/'); }}><img src={brandLogo} alt="猫子的世界"/></a><div className="authIntroCopy"><p className="authKicker">CAT'S WORLD</p><h1>{isLogin ? '欢迎回来。' : '从这里开始。'}</h1><p>{isLogin ? '登录后继续阅读、参与讨论，也可以进入属于你的工作台。' : '创建账户，完成邮箱验证后即可参与站内互动。'}</p></div><ul className="authBenefits"><li><span><Check size={15}/></span>安全的 HttpOnly 会话</li><li><span><Check size={15}/></span>邮箱验证后才可发表评论</li><li><span><Check size={15}/></span>仅按角色开放工作台权限</li></ul><div className="authSignal"><ShieldCheck size={19}/><span>一个安静记录技术与思考的角落</span></div></aside><section className="authCard"><div className="authCardHeader"><div className="authMark">猫</div><span>{isLogin ? '账户登录' : '创建账户'}</span></div><p className="authEyebrow">{isLogin ? 'WELCOME BACK' : 'JOIN THE COMMUNITY'}</p><h2>{isLogin ? '登录你的账号' : '创建你的账号'}</h2><p className="authLead">{isLogin ? '请输入账户信息，继续你的阅读与创作。' : '填写基本信息后，我们会发送一封验证邮件。'}</p><form onSubmit={submit}>{!isLogin && <Field icon={UserRound} label="显示名称" value={form.displayName} onChange={update('displayName')} required minLength="1" maxLength="120" autoComplete="name"/>}<Field icon={Mail} label="邮箱地址" type="email" value={form.email} onChange={update('email')} required autoComplete="email"/><Field icon={LockKeyhole} label="密码" type="password" value={form.password} onChange={update('password')} required minLength={isLogin ? 1 : 15} autoComplete={isLogin ? 'current-password' : 'new-password'}/>{!isLogin && <small className="authHint">至少 15 位，用于保护你的账户。</small>}{message && <p className="authMessage" role="status">{message}</p>}<button className="authSubmit" disabled={pending}>{pending ? '处理中…' : <>{isLogin ? '登录并继续' : '注册并发送验证邮件'}<ArrowRight size={17}/></>}</button></form><div className="authSwitchRow"><span>{isLogin ? '还没有账号？' : '已有账号？'}</span><button className="authSwitch" onClick={() => navigate(isLogin ? '/register' : '/login')}>{isLogin ? '创建账号' : '返回登录'}</button></div></section></div></main>;
}

export function VerifyEmailScreen({ navigate, location = window.location.pathname + window.location.search }) {
  const [message, setMessage] = useState('正在验证邮箱…');
  useEffect(() => {
    const token = new URLSearchParams(location.split('?')[1] || '').get('token');
    if (!token) { setMessage('验证链接无效。'); return; }
    authApi.verifyEmail(token).then(() => setMessage('邮箱已验证，现在可以登录。')).catch(error => setMessage(error instanceof ApiError ? error.message : '验证失败，请重试。'));
  }, [location]);
  return <main className="authPage"><div className="authGlow authGlowOne"/><div className="authGlow authGlowTwo"/><button className="backHome" onClick={() => navigate('/')}><ArrowLeft size={17}/>返回首页</button><section className="authCard authVerifyCard"><div className="authCardHeader"><div className="authMark">猫</div><span>邮箱验证</span></div><p className="authEyebrow">VERIFY EMAIL</p><h1>确认你的邮箱</h1><p className="authLead">验证完成后即可登录并参与讨论。</p><p className="authMessage" role="status">{message}</p><button className="authSubmit" onClick={() => navigate('/login')}>前往登录 <ArrowRight size={17}/></button></section></main>;
}
