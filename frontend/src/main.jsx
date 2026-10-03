import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowRight,
  ArrowUp,
  ArrowUpRight,
  FileText,
  Globe2,
  Image as ImageIcon,
  Languages,
  Mail,
  Rss,
  Search,
  Send,
  Sun,
  Moon,
  Clock3,
  Eye,
  MessageSquare,
  Menu,
  Smile,
} from 'lucide-react';
import { createRoot } from 'react-dom/client';
import brandLogo from './assets/cats-world-logo-ocean.webp';
import heroWorkspace from './assets/hero-workspace.webp';
import heroLight from './assets/hero-light-same-cat-daylight.webp';
import { AuthScreen, VerifyEmailScreen } from './auth';
import { ApiError, authApi, contentApi, rssFeedUrl } from './api/client';
import hljs from 'highlight.js/lib/common';
import DOMPurify from 'dompurify';
import './style.css';

const StudioApp = React.lazy(() => import('./studio').then((module) => ({ default: module.StudioApp })));

const nav = [
  ['首页', '/'],
  ['AI Lab', '/posts?q=AI'],
  ['故障档案', '/posts?q=故障'],
  ['实验室', '/posts?q=实践'],
  ['图库', '/photos'],
  ['关于', '/posts'],
];
const Arrow = () => <ArrowRight className="arrow" size={16} />;
function Logo({ iconOnly = false, footer = false }) {
  return (
    <span className={'brandLockup ' + (footer ? 'footerLogo' : '') + (iconOnly ? ' iconOnly' : '')} aria-label="猫子的世界">
      <img src={brandLogo} alt="猫子的世界" />
    </span>
  );
}
function HeaderMenuEntries({ items, parentId = null, go, depth = 0 }) {
  return (
    <>
      {items
        .filter((item) => (item.parentId || null) === parentId)
        .map((item) => {
          const children = items.some((child) => child.parentId === item.id);
          return (
            <div className={'navMenuEntry ' + (children ? 'hasChildren' : '')} key={item.id || `${item.label}-${item.url}`}>
              <a
                href={item.url}
                target={item.openNewTab ? '_blank' : undefined}
                rel={item.openNewTab ? 'noopener noreferrer' : undefined}
                className={(depth === 0 ? '' : 'subNavLink') + (item.label === 'AI Lab' ? ' labNav' : '')}
                onClick={item.url.startsWith('/') ? (event) => go(event, item.url) : undefined}
              >
                <NavIcon name={item.iconKey || item.label} />
                <span>{item.label}</span>
                {children && <small>⌄</small>}
              </a>
              {children && (
                <div className="navSubmenu">
                  <HeaderMenuEntries items={items} parentId={item.id} go={go} depth={depth + 1} />
                </div>
              )}
            </div>
          );
        })}
    </>
  );
}
const SEARCH_HISTORY_KEY = 'mmcat-search-history';
const SEARCH_HISTORY_LIMIT = 8;
function readSearchHistory() {
  try {
    const items = JSON.parse(window.localStorage.getItem(SEARCH_HISTORY_KEY) || '[]');
    return Array.isArray(items) ? items.filter((item) => item?.slug && item?.title).slice(0, SEARCH_HISTORY_LIMIT) : [];
  } catch {
    return [];
  }
}
function writeSearchHistory(items) {
  try {
    window.localStorage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(items));
  } catch {
    // Storage may be unavailable (private mode); history is a convenience only.
  }
}
function HeaderSearch({ navigate }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState(readSearchHistory);
  const [state, setState] = useState({ loading: false, items: [], error: '' });
  const [activeIndex, setActiveIndex] = useState(-1);
  const rootRef = useRef(null);
  const inputRef = useRef(null);
  const keyword = query.trim();

  // Suggestions: debounce typing and drop responses that a newer keystroke superseded.
  useEffect(() => {
    setActiveIndex(-1);
    if (!keyword) {
      setState({ loading: false, items: [], error: '' });
      return undefined;
    }
    const controller = new AbortController();
    setState((value) => ({ ...value, loading: true, error: '' }));
    const timer = window.setTimeout(() => {
      contentApi
        .posts({ q: keyword, pageSize: 6 }, { signal: controller.signal })
        .then((result) => setState({ loading: false, items: result.data || [], error: '' }))
        .catch((error) => {
          if (error.name !== 'AbortError') setState({ loading: false, items: [], error: error.message || '搜索失败，请稍后重试。' });
        });
    }, 220);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [keyword]);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutside = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener('mousedown', closeOnOutside);
    return () => document.removeEventListener('mousedown', closeOnOutside);
  }, [open]);

  useEffect(() => {
    const focusOnShortcut = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', focusOnShortcut);
    return () => window.removeEventListener('keydown', focusOnShortcut);
  }, []);

  const showingHistory = !keyword;
  const entries = showingHistory ? history : state.items;
  const close = () => {
    setOpen(false);
    inputRef.current?.blur();
  };
  const openPost = (post) => {
    // History records the articles a visitor actually opened from search.
    const next = [{ slug: post.slug, title: post.title }, ...history.filter((item) => item.slug !== post.slug)].slice(
      0,
      SEARCH_HISTORY_LIMIT,
    );
    setHistory(next);
    writeSearchHistory(next);
    setQuery('');
    close();
    navigate(`/archives/${encodeURIComponent(post.slug)}`);
  };
  const removeHistory = (slug) => {
    const next = history.filter((item) => item.slug !== slug);
    setHistory(next);
    writeSearchHistory(next);
  };
  const clearHistory = () => {
    setHistory([]);
    writeSearchHistory([]);
  };
  const submit = (event) => {
    event.preventDefault();
    if (activeIndex >= 0 && entries[activeIndex]) {
      openPost(entries[activeIndex]);
      return;
    }
    if (!keyword) return;
    close();
    navigate(`/posts?q=${encodeURIComponent(keyword)}`);
  };
  const onKeyDown = (event) => {
    if (event.key === 'Escape') {
      close();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!entries.length) return;
      event.preventDefault();
      setOpen(true);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActiveIndex((index) => (index + step + entries.length + (index < 0 && step < 0 ? 1 : 0)) % entries.length);
    }
  };

  return (
    <form className="search navSearch" ref={rootRef} onSubmit={submit} role="search">
      <Search size={16} />
      <input
        ref={inputRef}
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        placeholder="搜索文章、标签、问题..."
        aria-label="搜索文章、标签、问题"
        aria-expanded={open}
        aria-controls="header-search-panel"
        autoComplete="off"
      />
      <kbd>⌘ K</kbd>
      {open && (
        <div className="searchPanel" id="header-search-panel">
          <div className="searchPanelHead">
            <b>{showingHistory ? '最近看过' : `“${keyword}” 的相关文章`}</b>
            {showingHistory && history.length > 0 && (
              <button type="button" onClick={clearHistory}>
                清空
              </button>
            )}
          </div>
          {showingHistory ? (
            history.length ? (
              <ul>
                {history.map((item, index) => (
                  <li key={item.slug} className={index === activeIndex ? 'active' : ''}>
                    <button type="button" className="searchPanelItem" onClick={() => openPost(item)}>
                      <Clock3 size={14} />
                      <em>{item.title}</em>
                    </button>
                    <button
                      type="button"
                      className="searchPanelRemove"
                      onClick={() => removeHistory(item.slug)}
                      aria-label={`移除 ${item.title}`}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="searchPanelEmpty">还没有搜索记录，输入关键词试试。</p>
            )
          ) : state.error ? (
            <p className="searchPanelEmpty">{state.error}</p>
          ) : state.items.length ? (
            <>
              <ul>
                {state.items.map((item, index) => (
                  <li key={item.id} className={index === activeIndex ? 'active' : ''}>
                    <button type="button" className="searchPanelItem" onClick={() => openPost(item)}>
                      <FileText size={14} />
                      <em>
                        {item.title}
                        {item.summary && <small>{item.summary}</small>}
                      </em>
                    </button>
                  </li>
                ))}
              </ul>
              <button type="submit" className="searchPanelMore" onClick={() => setActiveIndex(-1)}>
                查看全部结果 <ArrowRight size={13} />
              </button>
            </>
          ) : (
            <p className="searchPanelEmpty">{state.loading ? '正在搜索…' : '没有找到相关文章。'}</p>
          )}
        </div>
      )}
    </form>
  );
}
function Header({ lightsOn, onToggle, onStudio, navigate, navigation = [], user }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const go = (event, to) => {
    event.preventDefault();
    setMenuOpen(false);
    navigate(to);
  };
  const items = navigation.filter((item) => item.location === 'header');
  const menuItems = items.length ? items : nav.map(([label, url]) => ({ label, url, iconKey: label, openNewTab: false, parentId: null }));
  return (
    <header className="siteHeader">
      <div className="wrap nav">
        <a className="brandAnchor" href="/" onClick={(event) => go(event, '/')} aria-label="猫子的世界首页">
          <span className="desktopBrand">
            <Logo />
          </span>
          <span className="mobileBrand">
            <Logo />
          </span>
        </a>
        <span className="brandDivider" />
        <nav className={'primaryNav ' + (menuOpen ? 'mobileOpen' : '')}>
          <HeaderMenuEntries items={menuItems} go={go} />
        </nav>
        <div className="navActions">
          <HeaderSearch navigate={navigate} />
          <span className="localeUnavailable" title="暂不开放">
            <button className="localeButton" disabled aria-label="中英文切换暂不开放">
              <Languages size={17} />
            </button>
          </span>
          <button className="sun themeButton" onClick={onToggle} aria-label={lightsOn ? '关闭灯光' : '打开灯光'}>
            {lightsOn ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          {user ? (
            <button className="profileButton" aria-label="进入工作台" onClick={onStudio}>
              {user.avatarUrl ? (
                <img src={user.avatarUrl} alt={user.displayName || user.email} />
              ) : (
                <span>{(user.displayName || user.email || '猫').slice(0, 1)}</span>
              )}
              <i />
            </button>
          ) : (
            <button className="loginButton" onClick={() => navigate('/login')}>
              登录
            </button>
          )}
        </div>
        <button className="mobileMenu" onClick={() => setMenuOpen((value) => !value)} aria-label="打开菜单" aria-expanded={menuOpen}>
          <Menu size={20} />
        </button>
      </div>
    </header>
  );
}
function AnimatedCount({ value, suffix = '' }) {
  const target = Number(value);
  const isNumber = Number.isFinite(target);
  const [display, setDisplay] = useState(isNumber ? 0 : null);
  useEffect(() => {
    if (!isNumber) {
      setDisplay(null);
      return undefined;
    }
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setDisplay(target);
      return undefined;
    }
    let frame = 0;
    const duration = Math.min(1250, 500 + Math.log10(Math.max(1, target)) * 190);
    const started = performance.now();
    const tick = (now) => {
      const progress = Math.min(1, (now - started) / duration);
      const eased = 1 - Math.pow(1 - progress, 4);
      setDisplay(Math.round(target * eased));
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [isNumber, target]);
  return (
    <b className="statValue">
      {display === null ? '—' : display}
      {suffix}
    </b>
  );
}
function Hero({ lightsOn, postCount, siteViewCount, navigate }) {
  return (
    <section className="hero">
      <div className="heroArt" style={{ backgroundImage: `url(${lightsOn ? heroLight : heroWorkspace})` }} />
      <div className="wash" />
      <div className="wrap heroText">
        <p className="eyebrow">//　用 AI 解决真实问题</p>
        <h1>
          让 AI 成为
          <br />
          <strong>解决问题的工具</strong>
        </h1>
        <p className="intro">
          记录用 AI 解决 Linux、网络、运维、开发等
          <br />
          实际问题的过程、思路和经验。
          <br />
          不只是提示词，更关注可落地的方法。
        </p>
        <div className="stats">
          <span>
            <AnimatedCount value={postCount} />
            AI 实战文章
          </span>
          <span>
            <AnimatedCount value={50} suffix="+" />
            问题场景
          </span>
          <span>
            <AnimatedCount value={100} suffix="+" />
            可复用方案
          </span>
          <span>
            <AnimatedCount value={siteViewCount} />
            站点访问量
          </span>
        </div>
        <div className="actions">
          <button onClick={() => navigate('/categories/ai')}>
            浏览 AI 解决方案 <Arrow />
          </button>
          <button onClick={() => navigate('/posts')}>查看全部文章</button>
        </div>
      </div>
    </section>
  );
}
const officialBrandIcons = {
  ChatGPT: ['https://api.iconify.design/logos:openai-icon.svg', 'mono'],
  Claude: ['https://api.iconify.design/simple-icons:claude.svg?color=%23e9784f', 'color'],
  Gemini: ['https://api.iconify.design/logos:google-gemini-icon.svg', 'color'],
  Cursor: ['https://api.iconify.design/logos:cursor-icon.svg', 'mono'],
  Copilot: ['https://api.iconify.design/logos:github-copilot.svg', 'mono'],
};
const iconifyToolIcons = {
  文档总结: 'https://api.iconify.design/ix:document-ai.svg?color=%2354d8ff',
  代码解释: 'https://api.iconify.design/tabler:code-ai.svg?color=%2335e1ad',
  Prompt: 'https://api.iconify.design/streamline:ai-prompt-spark.svg?color=%23d16dff',
  图像生成: 'https://api.iconify.design/streamline:ai-generate-portrait-image-spark.svg?color=%23f4f7fb',
};
const iconifyCategoryIcons = {
  'AI 解决方案': 'https://api.iconify.design/tabler:ai.svg?color=%23e766eb',
  Linux: 'https://api.iconify.design/logos:linux-tux.svg',
  网络: 'https://api.iconify.design/tabler:network.svg?color=%2333c5ef',
  '容器 & 云': 'https://api.iconify.design/logos:docker-icon.svg',
  数据库: 'https://api.iconify.design/tabler:database.svg?color=%236d89ff',
  安全: 'https://api.iconify.design/tabler:shield-check.svg?color=%235ce9b8',
  开发: 'https://api.iconify.design/tabler:code.svg?color=%23d66cf4',
  随笔: 'https://api.iconify.design/tabler:note.svg?color=%23ffbc4d',
};
Object.assign(iconifyCategoryIcons, {
  编程: 'https://api.iconify.design/tabler:code.svg?color=%23d66cf4',
  资讯: 'https://api.iconify.design/tabler:bulb.svg?color=%23ffbc4d',
  软件: 'https://api.iconify.design/tabler:apps.svg?color=%236d89ff',
  运维: 'https://api.iconify.design/tabler:server-cog.svg?color=%235ce9b8',
});
const iconifySectionIcons = {
  status: 'https://api.iconify.design/tabler:bolt.svg?color=%2338e5ad',
  latest: 'https://api.iconify.design/tabler:file-ai.svg?color=%2335c7f6',
  tools: 'https://api.iconify.design/tabler:ai.svg?color=%23df6ce6',
  tags: 'https://api.iconify.design/tabler:tags.svg?color=%23e6f3fc',
};
const iconifyNavIcons = {
  首页: 'https://api.iconify.design/tabler:home.svg?color=%2349bdff',
  'AI Lab': 'https://api.iconify.design/tabler:sparkles.svg?color=%23ca68f0',
  故障档案: 'https://api.iconify.design/tabler:archive.svg?color=%23b8c9d9',
  实验室: 'https://api.iconify.design/tabler:flask.svg?color=%23b8c9d9',
  图库: 'https://api.iconify.design/tabler:photo.svg?color=%23b8c9d9',
  关于: 'https://api.iconify.design/tabler:help-circle.svg?color=%23b8c9d9',
};
function NavIcon({ name }) {
  return (
    <img
      className="navIcon"
      src={iconifyNavIcons[name] || 'https://api.iconify.design/tabler:link.svg?color=%23b8c9d9'}
      alt=""
      aria-hidden="true"
    />
  );
}
function CategoryIcon({ name }) {
  const variant = name === 'Linux' ? ' is-linux' : name === '容器 & 云' ? ' is-container' : '';
  return (
    <img
      className={`categoryIcon${variant}`}
      src={iconifyCategoryIcons[name] || 'https://api.iconify.design/tabler:folder.svg?color=%2333c5ef'}
      alt=""
      aria-hidden="true"
    />
  );
}
function SectionIcon({ name }) {
  return <img className="sectionIcon" src={iconifySectionIcons[name]} alt="" aria-hidden="true" />;
}
function ToolIcon({ name }) {
  const official = officialBrandIcons[name];
  if (official) return <img className={`officialBrandIcon ${official[1]}`} src={official[0]} alt="" aria-hidden="true" />;
  if (iconifyToolIcons[name]) return <img className="iconifyToolIcon" src={iconifyToolIcons[name]} alt="" aria-hidden="true" />;
  const common = { viewBox: '0 0 48 48', 'aria-hidden': 'true' };
  return <ImageIcon {...common} strokeWidth="2.45" />;
}
function Categories({ categories = [], navigate }) {
  return (
    <section className="categoryArea">
      <div className="cats">
        {categories.length ? (
          categories.map((item, i) => (
            <button
              onClick={() => navigate(`/posts?category=${encodeURIComponent(item.slug)}`)}
              className={'catCard ' + (i ? '' : 'feature')}
              key={item.id}
            >
              <CategoryIcon name={item.name} />
              <b>{item.name}</b>
              <small>
                {item.postCount} 篇 <Arrow />
              </small>
            </button>
          ))
        ) : (
          <p className="dynamicEmpty">暂无分类，导入 Halo 数据或在工作台创建后将在这里显示。</p>
        )}
      </div>
    </section>
  );
}
function Post({ p, index, navigate }) {
  const category = p.categories?.[0] || '未分类';
  const tags = (p.tags || []).slice(0, 3);
  return (
    <article
      className="post postLink"
      tabIndex="0"
      role="link"
      onClick={() => navigate(`/archives/${encodeURIComponent(p.slug)}`)}
      onKeyDown={(event) => event.key === 'Enter' && navigate(`/archives/${encodeURIComponent(p.slug)}`)}
    >
      <div className={'cover ' + ['violet', 'blue', 'cyan', 'red', 'navy', 'indigo'][index % 6]}>
        {p.coverUrl && <img className="coverImage" src={p.coverUrl} alt="" />}
        <b className="coverBadge">{category}</b>
        {!p.coverUrl && (
          <div className="screen">
            <i />
            <i />
            <i />
            <i />
          </div>
        )}
      </div>
      <h2>{p.title}</h2>
      <p>{p.summary || '这篇文章暂未提供摘要。'}</p>
      {tags.length > 0 && (
        <div className="tags">
          {tags.map((tag) => (
            <span key={tag}>{tag}</span>
          ))}
        </div>
      )}
      <footer>
        <span>
          <Clock3 /> {p.publishedAt ? new Date(p.publishedAt).toLocaleDateString('zh-CN') : '草稿'}
        </span>
        <span>
          <Eye /> {p.viewCount || 0}
        </span>
        <span>
          <MessageSquare /> {p.commentCount || 0}
        </span>
      </footer>
    </article>
  );
}
function statusSummary(statusNodes) {
  if (statusNodes.every((item) => item.status === 'healthy')) return '正常运行';
  if (statusNodes.every((item) => !item.status || item.status === 'unknown')) return '等待检测';
  return '部分异常';
}
function StatusPanel({ statusNodes = [] }) {
  const loopNodes = statusNodes.length > 1 ? [...statusNodes, ...statusNodes] : statusNodes;
  if (!statusNodes.length) return null;
  return (
    <section className="status">
      <h3>
        <SectionIcon name="status" />
        站点状态{' '}
        <em>
          <i className="statusPulse" aria-hidden="true" />
          {statusSummary(statusNodes)}
        </em>
      </h3>
      <div className="statusNodeViewport">
        <div className={statusNodes.length > 1 ? 'statusNodeTrack isLooping' : 'statusNodeTrack'}>
          {loopNodes.map((item, index) => (
            <p key={`${item.id}-${index}`}>
              <i className="statusDot" aria-hidden="true" />
              {item.name}
              <span>{item.latencyMs == null ? '待检测' : `${item.latencyMs} ms`}</span>
            </p>
          ))}
        </div>
      </div>
    </section>
  );
}
function Side({ statusNodes = [], tags = [], tools = [], navigate }) {
  return (
    <aside>
      <StatusPanel statusNodes={statusNodes} />
      {tools.length > 0 && (
        <section className="panel">
          <h3>
            <SectionIcon name="tools" />
            常用 AI 工具{' '}
            <a
              href="/tools"
              onClick={(event) => {
                event.preventDefault();
                navigate('/tools');
              }}
            >
              更多工具 <Arrow />
            </a>
          </h3>
          <div className="tools">
            {tools.map((item) => (
              <a href={item.url} target="_blank" rel="noreferrer" key={item.id}>
                <span className="toolIcon">
                  <ToolIcon name={item.name} />
                </span>
                <b>{item.name}</b>
                <small>{item.description || 'AI 工具'}</small>
              </a>
            ))}
          </div>
        </section>
      )}
      {tags.length > 0 && (
        <section className="panel tagsPanel">
          <h3>
            <SectionIcon name="tags" />
            热门标签{' '}
            <a
              href="/tags"
              onClick={(event) => {
                event.preventDefault();
                navigate('/tags');
              }}
            >
              查看全部 <Arrow />
            </a>
          </h3>
          <div>
            {tags.map((item) => (
              <button onClick={() => navigate(`/tags/${encodeURIComponent(item.slug)}`)} key={item.id}>
                {item.name}　{item.postCount}
              </button>
            ))}
          </div>
        </section>
      )}
      <button className="journey" onClick={() => navigate('/studio')}>
        <Logo iconOnly />
        <span>
          <b>加入我的旅程</b>
          <small>记录、分享、成长</small>
        </span>
        <Arrow />
      </button>
    </aside>
  );
}
function Main({ home, navigate }) {
  const items = home?.posts || [];
  return (
    <main className="wrap main">
      <section className="homePrimary">
        <Categories categories={home?.homeCategories || home?.categories} navigate={navigate} />
        <div className="heading">
          <h2>
            <SectionIcon name="latest" />
            最新 AI 解决方案
          </h2>
          <a
            href="/posts"
            onClick={(event) => {
              event.preventDefault();
              navigate('/posts');
            }}
          >
            查看全部 <Arrow />
          </a>
        </div>
        {items.length ? (
          <div className="posts">
            {items.map((item, index) => (
              <Post p={item} index={index} navigate={navigate} key={item.id} />
            ))}
          </div>
        ) : (
          <p className="dynamicEmpty">暂无已发布文章。</p>
        )}
        <div className="quote">
          <b>“</b>
          <span>
            AI 不是替代思考，而是放大解决问题的能力。<small>— 某个晚上</small>
          </span>
          <i>
            Better Prompts
            <br />
            Bigger Solutions.
          </i>
        </div>
      </section>
      <Side statusNodes={home?.statusNodes} tags={home?.tags} tools={home?.tools} navigate={navigate} />
    </main>
  );
}
function Footer({ navigation = [], navigate }) {
  const footerItems = navigation.filter((item) => item.location === 'footer' && !item.parentId);
  const [visitorLocation, setVisitorLocation] = useState(null);
  useEffect(() => {
    const previousCallback = window.callback;
    const script = document.createElement('script');
    let active = true;
    window.callback = (ip, areaname, areacode, location) => {
      if (active) setVisitorLocation({ ip, areaName: areaname, areaCode: areacode, location });
    };
    script.src = 'https://ip.hzcat.com/jsonp/callback';
    script.async = true;
    document.body.appendChild(script);
    return () => {
      active = false;
      script.remove();
      if (previousCallback === undefined) delete window.callback;
      else window.callback = previousCallback;
    };
  }, []);
  const domain = window.location.hostname.toLowerCase();
  const filing = domain === 'mmcat.cn' || domain === 'www.mmcat.cn'
    ? { icp: '浙ICP备15008243号-1', police: '33010602012461' }
    : domain === 'hzcat.com' || domain === 'www.hzcat.com'
      ? { icp: '浙ICP备2024065820号-2', police: '33010602013467' }
      : { icp: '浙ICP备2024065820号-1', police: '33010602013467' };
  const follow = (event, item) => {
    if (item.openNewTab || !item.url.startsWith('/')) return;
    event.preventDefault();
    navigate(item.url);
  };
  return (
    <footer>
      <div className="wrap foot">
        <nav className="footerMenu">
          {footerItems.length ? (
            footerItems.map((item) => (
              <a
                href={item.url}
                key={item.id}
                target={item.openNewTab ? '_blank' : undefined}
                rel={item.openNewTab ? 'noopener noreferrer' : undefined}
                onClick={(event) => follow(event, item)}
              >
                {item.label}
              </a>
            ))
          ) : (
            <span>页脚菜单暂未配置</span>
          )}
        </nav>
        <span className="footerSocial" aria-label="社交链接">
          <a href="https://github.com/beanmtcat/cat-s-world" target="_blank" rel="noopener noreferrer" aria-label="GitHub：cat-s-world" title="查看 GitHub 仓库">
            <img className="footerGithub" src="https://api.iconify.design/tabler:brand-github.svg?color=%239ab7cb" alt="GitHub" />
          </a>
          <a href={rssFeedUrl} aria-label="订阅 RSS" title="订阅 RSS">
            <Rss />
          </a>
          <Mail />
          <Send />
        </span>
      </div>
      <div className="wrap copyright">
        <span>© 2026. All rights reserved.</span>
        <span className="footerBrand">
          <Logo footer />
          记录技术，也记录生活。
        </span>
        {visitorLocation && (
          <span className="footerVisitorIp">
            您当前的IP: {visitorLocation.ip} {visitorLocation.areaName} {visitorLocation.location}
            {visitorLocation.areaCode !== 'CN' && visitorLocation.areaCode && ` (${visitorLocation.areaCode})`}
          </span>
        )}
        {visitorLocation?.areaCode === 'CN' && (
          <span className="footerFiling">
            <a className="icpRecord" href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">{filing.icp}</a>
            <a className="icpRecord" href={`https://www.beian.gov.cn/portal/registerSystemInfo?recordcode=${filing.police}`} target="_blank" rel="noopener noreferrer">浙公网安备 {filing.police}号</a>
          </span>
        )}
        <span className="footerSlogan">用技术让生活更有趣。</span>
      </div>
    </footer>
  );
}
function PublicShell({ children, lightsOn, onToggle, navigate, locale }) {
  const [navigation, setNavigation] = useState([]);
  const [user, setUser] = useState(null);
  const [showBackToTop, setShowBackToTop] = useState(false);
  const activeLocale = locale || 'zh-CN';
  useEffect(() => {
    if (document.querySelector('link[rel="alternate"][type="application/rss+xml"]')) return;
    const link = document.createElement('link');
    link.rel = 'alternate';
    link.type = 'application/rss+xml';
    link.title = '猫子的世界 RSS';
    link.href = rssFeedUrl;
    document.head.appendChild(link);
  }, []);
  useEffect(() => {
    let active = true;
    contentApi
      .navigation(activeLocale)
      .then((result) => {
        if (active) setNavigation(result.data);
      })
      .catch(() => {});
    authApi
      .me()
      .then((result) => active && setUser(result.data))
      .catch(() => active && setUser(null));
    return () => {
      active = false;
    };
  }, [activeLocale]);
  useEffect(() => {
    const update = () => setShowBackToTop(window.scrollY > 420);
    update();
    window.addEventListener('scroll', update, { passive: true });
    return () => window.removeEventListener('scroll', update);
  }, []);
  return (
    <div className={'appShell ' + (lightsOn ? 'lightsOn' : '')}>
      <Header
        lightsOn={lightsOn}
        onToggle={onToggle}
        onStudio={() => navigate('/studio')}
        navigate={navigate}
        navigation={navigation}
        user={user}
      />
      {children}
      <Footer navigation={navigation} navigate={navigate} />
      <button
        className={`backToTop${showBackToTop ? ' isVisible' : ''}`}
        onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
        aria-label="回到页面顶部"
        title="回到顶部"
      >
        <ArrowUp size={20} />
      </button>
    </div>
  );
}
function ListRow({ post, navigate }) {
  const date = post.publishedAt ? new Date(post.publishedAt) : null;
  const tags = (post.tags || []).slice(0, 3);
  return (
    <article
      className="listRow"
      role="link"
      tabIndex="0"
      onClick={() => navigate(`/archives/${encodeURIComponent(post.slug)}`)}
      onKeyDown={(event) => event.key === 'Enter' && navigate(`/archives/${encodeURIComponent(post.slug)}`)}
    >
      <time>
        {date ? (
          <>
            <b>
              {String(date.getMonth() + 1).padStart(2, '0')}-{String(date.getDate()).padStart(2, '0')}
            </b>
            <span>{date.getFullYear()}</span>
          </>
        ) : (
          <b>草稿</b>
        )}
      </time>
      <div className="listCopy">
        <h2>{post.title}</h2>
        <p>{post.summary || '这篇文章暂未提供摘要。'}</p>
        <div className="listRowMeta">
          {tags.map((tag) => (
            <span key={tag}>{tag}</span>
          ))}
          <em className="listMetrics">
            <Eye size={14} />
            {post.viewCount || 0}
          </em>
          <em className="listMetrics">
            <MessageSquare size={14} />
            {post.commentCount || 0}
          </em>
        </div>
      </div>
      {post.coverUrl ? (
        <img src={post.coverUrl} alt="" />
      ) : (
        <div className="listNoCover">
          <FileText size={24} />
        </div>
      )}
    </article>
  );
}
function PublicSidebar({ home, navigate }) {
  return (
    <aside className="publicSidebar">
      {(home?.statusNodes || []).length > 0 && (
        <section className="sidebarStatus">
          <h3>
            <SectionIcon name="status" />
            当前状态{' '}
            <em>
              <i />
              {statusSummary(home.statusNodes)}
            </em>
          </h3>
          {home.statusNodes.map((item) => (
            <p key={item.id}>
              <i />
              {item.name}
              <small>{item.latencyMs == null ? '—' : `${item.latencyMs} ms`}</small>
            </p>
          ))}
        </section>
      )}
      <section>
        <h3>正在探索</h3>
        <div className="exploreLinks">
          {(home?.categories || []).map((item) => (
            <button key={item.id} onClick={() => navigate(`/posts?category=${encodeURIComponent(item.slug)}`)}>
              →　{item.name}
              <small>{item.postCount}</small>
            </button>
          ))}
        </div>
      </section>
      <section>
        <h3>热门标签</h3>
        <div className="sidebarTags">
          {(home?.tags || []).slice(0, 14).map((item) => (
            <button key={item.id} onClick={() => navigate(`/posts?tag=${encodeURIComponent(item.slug)}`)}>
              {item.name}
              <small>{item.postCount}</small>
            </button>
          ))}
        </div>
      </section>
      <section className="sidebarQuote">
        <b>“</b>
        <p>
          把复杂的问题，拆成每一次可验证的实践。<small>— 猫子</small>
        </p>
      </section>
    </aside>
  );
}
function PostListScreen({ lightsOn, onToggle, navigate, site }) {
  const params = new URLSearchParams(window.location.search);
  const path = window.location.pathname;
  const q = params.get('q') || '';
  const category = path.startsWith('/categories/') ? decodeURIComponent(path.slice('/categories/'.length)) : params.get('category') || '';
  const tag = path.startsWith('/tags/') ? decodeURIComponent(path.slice('/tags/'.length)) : params.get('tag') || '';
  const page = Math.max(1, Number(params.get('page')) || 1);
  const pageSize = String(category ? site?.categoryPostsPerPage || 12 : tag ? site?.tagPostsPerPage || 12 : site?.postsPerPage || 12);
  const [state, setState] = useState({ loading: true, items: [], total: 0, totalPages: 0, error: '' });
  const [home, setHome] = useState(null);
  useEffect(() => {
    let active = true;
    setState((value) => ({ ...value, loading: true, error: '' }));
    Promise.all([contentApi.posts({ q, category, tag, page, pageSize }), contentApi.home()])
      .then(([posts, homepage]) => {
        if (!active) return;
        setState({ loading: false, items: posts.data, total: posts.meta?.total || 0, totalPages: posts.meta?.totalPages || 0, error: '' });
        setHome(homepage.data);
      })
      .catch(
        (error) => active && setState({ loading: false, items: [], total: 0, totalPages: 0, error: error.message || '文章列表加载失败。' }),
      );
    return () => {
      active = false;
    };
  }, [q, category, tag, page, pageSize]);
  const categoryName = home?.categories?.find((item) => item.slug === category)?.name;
  const tagName = home?.tags?.find((item) => item.slug === tag)?.name;
  const title = q ? `搜索：${q}` : category ? categoryName || '分类文章' : tag ? tagName || '标签文章' : '最新文章';
  const goPage = (target) => {
    const next = new URLSearchParams();
    if (q) next.set('q', q);
    if (target > 1) next.set('page', String(target));
    const base = category ? `/categories/${encodeURIComponent(category)}` : tag ? `/tags/${encodeURIComponent(tag)}` : '/posts';
    navigate(`${base}${next.size ? `?${next}` : ''}`);
  };
  const pages = Array.from({ length: state.totalPages }, (_, index) => index + 1).filter(
    (value) => value === 1 || value === state.totalPages || Math.abs(value - page) <= 1,
  );
  return (
    <PublicShell lightsOn={lightsOn} onToggle={onToggle} navigate={navigate}>
      <main className="wrap listingPage">
        <header className="listingHead">
          <h1>{title}</h1>
          <span>共记录 {state.total || '—'} 篇内容，以问题、过程和可复用方案为线索。</span>
        </header>
        <div className="listingLayout">
          <section className="articleList">
            {state.error ? (
              <p className="dynamicError">{state.error}</p>
            ) : state.loading ? (
              <p className="dynamicEmpty">正在加载文章…</p>
            ) : state.items.length ? (
              <>
                {state.items.map((item) => (
                  <ListRow post={item} navigate={navigate} key={item.id} />
                ))}
                {state.totalPages > 1 && (
                  <nav className="listingPagination" aria-label="文章分页">
                    <button disabled={page === 1} onClick={() => goPage(page - 1)}>
                      上一页
                    </button>
                    {pages.map((value, index) => (
                      <React.Fragment key={value}>
                        {index > 0 && value - pages[index - 1] > 1 && <i>…</i>}
                        <button className={value === page ? 'active' : ''} onClick={() => goPage(value)}>
                          {value}
                        </button>
                      </React.Fragment>
                    ))}
                    <button disabled={page === state.totalPages} onClick={() => goPage(page + 1)}>
                      下一页
                    </button>
                  </nav>
                )}
              </>
            ) : (
              <p className="dynamicEmpty">没有匹配的已发布文章。</p>
            )}
          </section>
          <PublicSidebar home={home} navigate={navigate} />
        </div>
      </main>
    </PublicShell>
  );
}
function contentText(value) {
  const text =
    typeof value === 'string'
      ? value
      : value == null
        ? ''
        : (() => {
            try {
              return JSON.stringify(value);
            } catch {
              return '';
            }
          })();
  return text.replace(/^(!\[[^\]]*\]\([^\n)]+\))\s*(.+)$/gm, '$1\n$2');
}
function legacyHaloText(content) {
  const source = contentText(content);
  try {
    const patches = JSON.parse(source);
    if (!Array.isArray(patches)) return source;
    const html = patches.map((item) => item?.target?.lines?.join('\n') || '').join('\n');
    return new DOMParser().parseFromString(html, 'text/html').body.textContent?.trim() || source;
  } catch {
    return source;
  }
}
function InlineMarkdown({ value }) {
  const parts = value.split(/(\[[^\]]+\]\([^\s)]+\)|`[^`]+`|\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, index) => {
        const link = part.match(/^\[([^\]]+)\]\(([^\s)]+)\)$/);
        if (link) {
          const safe = /^https?:\/\//i.test(link[2]);
          return safe ? (
            <a key={index} href={link[2]} target="_blank" rel="noreferrer">
              {link[1]}
            </a>
          ) : (
            link[1]
          );
        }
        if (part.startsWith('`') && part.endsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>;
        if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>;
        return part;
      })}
    </>
  );
}
function articleHeadings(content) {
  return contentText(content)
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => line.match(/^(#{1,6})\s+(.+)/))
    .filter(Boolean)
    .slice(0, 40)
    .map((match, index) => ({ id: `section-${index}`, depth: Math.min(match[1].length, 6), title: match[2].replace(/[*_`]/g, '').trim() }));
}
function htmlHeadings(content) {
  const document = new DOMParser().parseFromString(contentText(content), 'text/html');
  return [...document.querySelectorAll('h2,h3,h4,h5,h6')]
    .slice(0, 40)
    .map((node, index) => ({ id: `section-${index}`, depth: Number(node.tagName.slice(1)) - 1, title: (node.textContent || '').trim() }))
    .filter((item) => item.title);
}
function ArticleToc({ headings }) {
  const baseDepth = Math.min(...headings.map((item) => item.depth));
  return (
    <nav className="articleToc" aria-label="文章目录">
      {headings.map((entry) => {
        const level = Math.max(0, entry.depth - baseDepth);
        return (
          <a
            className={`depth-${Math.min(entry.depth, 6)}`}
            style={{ '--toc-depth': level }}
            href={`#${entry.id}`}
            key={entry.id}
            aria-level={level + 1}
          >
            {entry.title}
          </a>
        );
      })}
    </nav>
  );
}
function tableCells(line) {
  return line
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((cell) => cell.trim().replace(/\\\|/g, '|'));
}
function isTableDivider(line) {
  return tableCells(line).every((cell) => /^:?-{3,}:?$/.test(cell));
}
const HighlightedCode = React.memo(function HighlightedCode({ code, language }) {
  const selected = useMemo(
    () => (language && hljs.getLanguage(language) ? hljs.highlight(code, { language }).value : hljs.highlightAuto(code).value),
    [code, language],
  );
  return (
    <pre>
      <code className="hljs" dangerouslySetInnerHTML={{ __html: selected }} />
    </pre>
  );
});
const NO_HEADINGS = [];
const MarkdownBody = React.memo(function MarkdownBody({ content, headings = NO_HEADINGS }) {
  const lines = contentText(content).replace(/\r\n/g, '\n').split('\n');
  const blocks = [];
  let index = 0;
  let headingIndex = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (/^```/.test(line.trim())) {
      const language = line.trim().slice(3).trim();
      const code = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index].trim())) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push(<HighlightedCode key={`code-${index}`} code={code.join('\n')} language={language} />);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)/);
    if (heading) {
      const Tag = `h${Math.min(heading[1].length + 1, 4)}`;
      const headingId = headings[headingIndex++]?.id;
      blocks.push(
        <Tag id={headingId} key={`heading-${index}`}>
          <InlineMarkdown value={heading[2]} />
        </Tag>,
      );
      index += 1;
      continue;
    }
    if (line.includes('|') && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
      const headers = tableCells(line);
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(tableCells(lines[index]));
        index += 1;
      }
      blocks.push(
        <div className="articleTableWrap" key={`table-${index}`}>
          <table>
            <thead>
              <tr>
                {headers.map((cell, column) => (
                  <th key={column}>
                    <InlineMarkdown value={cell} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {headers.map((_, column) => (
                    <td key={column}>
                      <InlineMarkdown value={row[column] || ''} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    const image = line.match(/^!\[([^\]]*)\]\(([^\s)]+)\)$/);
    if (image) {
      blocks.push(<img className="articleInlineImage" key={`image-${index}`} src={image[2]} alt={image[1] || ''} />);
      index += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index])) {
        items.push(
          <li key={index}>
            <InlineMarkdown value={lines[index].replace(/^[-*]\s+/, '')} />
          </li>,
        );
        index += 1;
      }
      blocks.push(<ul key={`list-${index}`}>{items}</ul>);
      continue;
    }
    const paragraph = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^```/.test(lines[index].trim()) &&
      !/^(#{1,6})\s+/.test(lines[index]) &&
      !/^!\[/.test(lines[index]) &&
      !/^[-*]\s+/.test(lines[index])
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push(
      <p key={`paragraph-${index}`}>
        <InlineMarkdown value={paragraph.join(' ')} />
      </p>,
    );
  }
  return <div className="articleBody">{blocks}</div>;
});
const HTML_SANITIZE_OPTIONS = {
  ALLOWED_TAGS: [
    'p',
    'br',
    'hr',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'ul',
    'ol',
    'li',
    'strong',
    'em',
    'b',
    'i',
    's',
    'u',
    'del',
    'code',
    'pre',
    'blockquote',
    'a',
    'img',
    'table',
    'thead',
    'tbody',
    'tr',
    'th',
    'td',
  ],
  ALLOWED_ATTR: ['href', 'title', 'src', 'alt', 'width', 'height', 'colspan', 'rowspan'],
  // http(s), mailto, site-rooted paths (never protocol-relative) and in-page anchors only.
  ALLOWED_URI_REGEXP: /^(?:https?:|mailto:|\/(?!\/)|#)/i,
  ALLOW_DATA_ATTR: false,
  RETURN_DOM: true,
};
function sanitizedHtml(content, headings = NO_HEADINGS) {
  // The API sanitizes rich text when it is saved; this second pass protects
  // against legacy rows and anything written before that rule existed.
  const body = DOMPurify.sanitize(contentText(content), HTML_SANITIZE_OPTIONS);
  [...body.querySelectorAll('h2,h3,h4,h5,h6')].forEach((node, index) => {
    if (headings[index]) node.id = headings[index].id;
  });
  return body.innerHTML;
}
const HtmlBody = React.memo(function HtmlBody({ content, headings = NO_HEADINGS }) {
  const html = useMemo(() => sanitizedHtml(content, headings), [content, headings]);
  return <div className="articleBody htmlArticleBody" dangerouslySetInnerHTML={{ __html: html }} />;
});
function commentThreadCount(comment) {
  return 1 + (comment.replies || []).reduce((total, reply) => total + commentThreadCount(reply), 0);
}
function CommentThread({ comment, depth = 0 }) {
  return (
    <article className="commentItem" data-depth={depth}>
      <div className="commentAvatar" aria-hidden="true">
        {(comment.authorName || '访').slice(0, 1)}
      </div>
      <div className="commentContent">
        <div className="commentItemMeta">
          <strong>{comment.authorName}</strong>
          <time>{new Date(comment.ctime).toLocaleString('zh-CN')}</time>
        </div>
        <p>{comment.content}</p>
        {comment.replies?.length > 0 && (
          <div className="commentChildren">
            {comment.replies.map((reply) => (
              <CommentThread comment={reply} depth={depth + 1} key={reply.id} />
            ))}
          </div>
        )}
      </div>
    </article>
  );
}
function ArticleComments({ postId, navigate, onApprovedCount }) {
  const [comments, setComments] = useState([]);
  const [user, setUser] = useState(null);
  const [content, setContent] = useState('');
  const [emojiOpen, setEmojiOpen] = useState(false);
  const [status, setStatus] = useState({ loading: true, message: '', posting: false });
  const emoji = ['😀', '😂', '😊', '😍', '🤔', '😅', '😭', '😎', '👍', '👎', '👏', '🎉', '🔥', '❤️', '✨', '🐱'];
  const displayedCount = comments.reduce((total, comment) => total + commentThreadCount(comment), 0);
  const countResult = (result) => (result.data || []).reduce((total, comment) => total + commentThreadCount(comment), 0);
  const load = useCallback(
    () =>
      contentApi
        .comments({ postId, pageSize: 100 })
        .then((result) => {
          setComments(result.data || []);
          onApprovedCount?.(countResult(result));
        })
        .catch((error) => setStatus((value) => ({ ...value, message: error.message || '评论加载失败。' }))),
    [postId, onApprovedCount],
  );
  useEffect(() => {
    let active = true;
    setStatus({ loading: true, message: '', posting: false });
    Promise.all([contentApi.comments({ postId, pageSize: 100 }), authApi.me().catch(() => null)])
      .then(([result, profile]) => {
        if (!active) return;
        setComments(result.data || []);
        onApprovedCount?.(countResult(result));
        setUser(profile?.data || null);
        setStatus({ loading: false, message: '', posting: false });
      })
      .catch((error) => active && setStatus({ loading: false, message: error.message || '评论加载失败。', posting: false }));
    return () => {
      active = false;
    };
  }, [postId, onApprovedCount]);
  const submit = async (event) => {
    event.preventDefault();
    const text = content.trim();
    if (!text) return;
    setStatus((value) => ({ ...value, posting: true, message: '' }));
    try {
      await contentApi.createComment({ targetType: 'post', targetId: postId, content: text });
      setContent('');
      setEmojiOpen(false);
      setStatus({ loading: false, posting: false, message: '评论已提交，审核通过后会显示在这里。' });
      load();
    } catch (error) {
      setStatus((value) => ({ ...value, posting: false, message: error.message || '评论发送失败。' }));
    }
  };
  return (
    <section className="articleComments" aria-label="评论">
      <div className="articleCommentsHead">
        <h2>评论</h2>
        <span>{displayedCount} 条已发布评论</span>
      </div>
      {status.loading ? (
        <p className="commentEmpty">正在加载评论…</p>
      ) : comments.length ? (
        <div className="commentList">
          {comments.map((comment) => (
            <CommentThread comment={comment} key={comment.id} />
          ))}
        </div>
      ) : (
        <p className="commentEmpty">还没有评论，来说两句吧。</p>
      )}
      {user ? (
        <form className="commentForm" onSubmit={submit}>
          <label htmlFor="article-comment">发表评论</label>
          <textarea
            id="article-comment"
            value={content}
            onChange={(event) => setContent(event.target.value)}
            maxLength="5000"
            placeholder="写下你的想法…"
          />
          <div className="commentActions">
            <div className="emojiPicker">
              <button
                type="button"
                className="emojiTrigger"
                onClick={() => setEmojiOpen((value) => !value)}
                aria-label="插入表情"
                aria-expanded={emojiOpen}
              >
                <Smile size={17} /> 表情
              </button>
              {emojiOpen && (
                <div className="emojiPanel">
                  {emoji.map((item) => (
                    <button
                      type="button"
                      key={item}
                      onClick={() => {
                        setContent((value) => `${value}${item}`);
                        setEmojiOpen(false);
                      }}
                      aria-label={`插入${item}`}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <small>评论需要审核后才会公开显示。</small>
            <button type="submit" disabled={status.posting}>
              {status.posting ? '发送中…' : '发表评论'} <Send size={14} />
            </button>
          </div>
        </form>
      ) : (
        <div className="commentLogin">
          <span>登录并验证邮箱后可以发表评论。</span>
          <button onClick={() => navigate(`/login?next=${encodeURIComponent(window.location.pathname)}`)}>登录后评论</button>
        </div>
      )}
      {status.message && <p className="commentNotice">{status.message}</p>}
    </section>
  );
}

function ArticleScreen({ slug, lightsOn, onToggle, navigate }) {
  const [state, setState] = useState({ loading: true, item: null, error: '' });
  const [home, setHome] = useState(null);
  const [commentCount, setCommentCount] = useState(0);
  useEffect(() => {
    let active = true;
    Promise.all([contentApi.post(slug), contentApi.home()])
      .then(
        ([post, homepage]) =>
          active &&
          (setState({ loading: false, item: post.data, error: '' }), setCommentCount(post.data.commentCount || 0), setHome(homepage.data)),
      )
      .catch((error) => active && setState({ loading: false, item: null, error: error.message || '文章加载失败。' }));
    return () => {
      active = false;
    };
  }, [slug]);
  const item = state.item;
  const isHtml = item?.contentFormat === 'html';
  // Parsing and syntax highlighting are expensive; redo them only when the
  // article itself changes, not when comment counts or sidebar data arrive.
  const body = useMemo(() => (item ? (isHtml ? contentText(item.content || '') : legacyHaloText(item.content || '')) : ''), [item, isHtml]);
  const headings = useMemo(() => (isHtml ? htmlHeadings(body) : articleHeadings(body)), [body, isHtml]);
  const date = item?.publishedAt ? new Date(item.publishedAt).toLocaleDateString('zh-CN') : '草稿';
  return (
    <PublicShell lightsOn={lightsOn} onToggle={onToggle} navigate={navigate}>
      <main className="wrap articlePage">
        {state.loading ? (
          <p className="dynamicEmpty">正在加载文章…</p>
        ) : state.error ? (
          <>
            <button className="backButton" onClick={() => navigate('/posts')}>
              ← 返回文章列表
            </button>
            <p className="dynamicError">{state.error}</p>
          </>
        ) : (
          <div className="articleLayout">
            <aside className="articleLeftRail">
              <h3>分类</h3>
              {(home?.categories || []).map((category) => (
                <button key={category.id} onClick={() => navigate(`/categories/${encodeURIComponent(category.slug)}`)}>
                  <CategoryIcon name={category.name} />
                  <span>{category.name}</span>
                  <small>{category.postCount}</small>
                </button>
              ))}
            </aside>
            <article className="articleDocument">
              <button className="backButton" onClick={() => navigate('/posts')}>
                ← 返回文章列表
              </button>
              <h1>{item.title}</h1>
              <div className="articleMeta articleMetrics">
                <span>
                  <Clock3 /> {date}
                </span>
                <span>
                  <Eye /> {item.viewCount || 0}
                </span>
                <span>
                  <MessageSquare /> {commentCount}
                </span>
              </div>
              <div className="articleTaxonomy">
                {(item.categories || []).map((category) => (
                  <button
                    className="categoryPill"
                    key={category.slug}
                    onClick={() => navigate(`/categories/${encodeURIComponent(category.slug)}`)}
                  >
                    分类：{category.name}
                  </button>
                ))}
                {(item.tags || []).map((tag) => (
                  <button className="tagPill" key={tag.slug} onClick={() => navigate(`/tags/${encodeURIComponent(tag.slug)}`)}>
                    #{tag.name}
                  </button>
                ))}
              </div>
              {item.coverUrl && <img className="articleCover" src={item.coverUrl} alt="" />}
              {isHtml ? <HtmlBody content={body} headings={headings} /> : <MarkdownBody content={body} headings={headings} />}
              <ArticleComments postId={item.id} navigate={navigate} onApprovedCount={setCommentCount} />
            </article>
            <aside className="articleRightRail">
              <section>
                <h3>目录</h3>
                {headings.length ? <ArticleToc headings={headings} /> : <p>本文暂无目录。</p>}
              </section>
              <section>
                <h3>热门标签</h3>
                <div className="sidebarTags">
                  {(home?.tags || []).slice(0, 10).map((tag) => (
                    <button key={tag.id} onClick={() => navigate(`/tags/${encodeURIComponent(tag.slug)}`)}>
                      {tag.name}
                      <small>{tag.postCount}</small>
                    </button>
                  ))}
                </div>
              </section>
            </aside>
          </div>
        )}
      </main>
    </PublicShell>
  );
}
function PageScreen({ slug, lightsOn, onToggle, navigate }) {
  const [state, setState] = useState({ loading: true, item: null, error: '' });
  useEffect(() => {
    let active = true;
    setState({ loading: true, item: null, error: '' });
    contentApi
      .page(slug)
      .then((result) => active && setState({ loading: false, item: result.data, error: '' }))
      .catch(async (error) => {
        if (!active) return;
        if (error instanceof ApiError && error.status === 404) {
          // An unknown path may be a legacy address recorded by the importer.
          const redirect = await contentApi.resolveRedirect(window.location.pathname + window.location.search).catch(() => null);
          if (!active) return;
          const toPath = redirect?.data?.toPath;
          if (toPath?.startsWith('/') && !toPath.startsWith('//')) {
            navigate(toPath, { replace: true });
            return;
          }
        }
        setState({ loading: false, item: null, error: error.message || '页面加载失败。' });
      });
    return () => {
      active = false;
    };
  }, [slug, navigate]);
  const item = state.item;
  return (
    <PublicShell lightsOn={lightsOn} onToggle={onToggle} navigate={navigate}>
      <main className="wrap articlePage">
        <article className="articleDocument">
          {state.loading ? (
            <p className="dynamicEmpty">正在加载页面…</p>
          ) : state.error ? (
            <p className="dynamicError">{state.error}</p>
          ) : (
            <>
              <h1>{item.title}</h1>
              {item.contentFormat === 'html' ? <HtmlBody content={item.content} /> : <MarkdownBody content={item.content} />}
            </>
          )}
        </article>
      </main>
    </PublicShell>
  );
}
function TagsScreen({ lightsOn, onToggle, navigate }) {
  const [state, setState] = useState({ loading: true, items: [], error: '' });
  useEffect(() => {
    let active = true;
    contentApi
      .tags()
      .then((result) => active && setState({ loading: false, items: result.data || [], error: '' }))
      .catch((error) => active && setState({ loading: false, items: [], error: error.message || '标签加载失败。' }));
    return () => {
      active = false;
    };
  }, []);
  return (
    <PublicShell lightsOn={lightsOn} onToggle={onToggle} navigate={navigate}>
      <main className="wrap tagIndexPage">
        <header>
          <p className="eyebrow">TOPICS</p>
          <h1>标签</h1>
          <span>按主题浏览文章与实践记录。</span>
        </header>
        {state.loading ? (
          <p className="dynamicEmpty">正在加载标签…</p>
        ) : state.error ? (
          <p className="dynamicError">{state.error}</p>
        ) : state.items.length ? (
          <section className="tagIndexCloud" aria-label="全部标签">
            {state.items.map((tag) => (
              <button key={tag.id} onClick={() => navigate(`/tags/${encodeURIComponent(tag.slug)}`)}>
                <b># {tag.name}</b>
                <small>{tag.postCount}</small>
              </button>
            ))}
          </section>
        ) : (
          <p className="dynamicEmpty">暂无标签。</p>
        )}
      </main>
    </PublicShell>
  );
}
function toolHost(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}
function ToolsScreen({ lightsOn, onToggle, navigate }) {
  const [state, setState] = useState({ loading: true, items: [], error: '' });
  const [category, setCategory] = useState('all');
  useEffect(() => {
    let active = true;
    contentApi
      .tools()
      .then((result) => active && setState({ loading: false, items: result.data || [], error: '' }))
      .catch((error) => active && setState({ loading: false, items: [], error: error.message || 'AI 工具加载失败。' }));
    return () => {
      active = false;
    };
  }, []);
  const categoryOf = (item) => item.category || '其他工具';
  // Keep categories in the order their first tool appears, which follows the curated sort order.
  const categories = [...new Set(state.items.map(categoryOf))];
  const visible = category === 'all' ? state.items : state.items.filter((item) => categoryOf(item) === category);
  return (
    <PublicShell lightsOn={lightsOn} onToggle={onToggle} navigate={navigate}>
      <main className="wrap toolsPage">
        <header>
          <p className="eyebrow">TOOLBOX</p>
          <h1>AI 工具</h1>
          <span>精选的 AI 产品与实用服务，点击即可前往官方网站。</span>
        </header>
        {state.loading ? (
          <p className="dynamicEmpty">正在加载 AI 工具…</p>
        ) : state.error ? (
          <p className="dynamicError">{state.error}</p>
        ) : state.items.length ? (
          <>
            <div className="toolFilters" role="tablist" aria-label="按用途筛选工具">
              <button
                role="tab"
                aria-selected={category === 'all'}
                className={category === 'all' ? 'active' : ''}
                onClick={() => setCategory('all')}
              >
                全部<small>{state.items.length}</small>
              </button>
              {categories.map((name) => (
                <button
                  role="tab"
                  aria-selected={category === name}
                  className={category === name ? 'active' : ''}
                  onClick={() => setCategory(name)}
                  key={name}
                >
                  {name}
                  <small>{state.items.filter((item) => categoryOf(item) === name).length}</small>
                </button>
              ))}
            </div>
            <div className="toolGrid">
              {visible.map((item) => (
                <a className="toolCard" href={item.url} target="_blank" rel="noopener noreferrer" key={item.id}>
                  <div className="toolCardTop">
                    <span className="toolCardIcon">
                      <ToolIcon name={item.name} />
                    </span>
                    <div>
                      <b>{item.name}</b>
                      {item.provider && <small>{item.provider}</small>}
                    </div>
                    <ArrowUpRight className="toolCardGo" size={18} aria-hidden="true" />
                  </div>
                  <p>{item.description || '访问官方网站了解更多。'}</p>
                  <div className="toolCardFoot">
                    <em>{categoryOf(item)}</em>
                    <span>{toolHost(item.url)}</span>
                  </div>
                </a>
              ))}
            </div>
          </>
        ) : (
          <p className="dynamicEmpty">暂无 AI 工具。</p>
        )}
      </main>
    </PublicShell>
  );
}
function FriendAvatar({ item }) {
  // Remote logos disappear over time; fall back to the site's initial instead of an empty circle.
  const [failed, setFailed] = useState(false);
  if (item.logoUrl && !failed)
    return <img className="friendAvatar" src={item.logoUrl} alt="" loading="lazy" onError={() => setFailed(true)} />;
  return (
    <span className="friendAvatar isFallback" aria-hidden="true">
      {(item.name || '友').trim().slice(0, 1).toUpperCase()}
    </span>
  );
}
function FriendLinksScreen({ lightsOn, onToggle, navigate }) {
  const [state, setState] = useState({ loading: true, items: [], error: '' });
  useEffect(() => {
    let active = true;
    contentApi
      .friendLinks()
      .then((result) => active && setState({ loading: false, items: result.data || [], error: '' }))
      .catch((error) => active && setState({ loading: false, items: [], error: error.message || '友情链接加载失败。' }));
    return () => {
      active = false;
    };
  }, []);
  return (
    <PublicShell lightsOn={lightsOn} onToggle={onToggle} navigate={navigate}>
      <main className="wrap toolsPage friendLinksPage">
        <header>
          <p className="eyebrow">FRIENDS</p>
          <h1>友情链接</h1>
          <span>与值得关注的朋友保持连接{state.items.length > 0 && `，目前共 ${state.items.length} 位邻居`}。</span>
        </header>
        {state.loading ? (
          <p className="dynamicEmpty">正在加载…</p>
        ) : state.error ? (
          <p className="dynamicError">{state.error}</p>
        ) : state.items.length ? (
          <section className="friendGrid">
            {state.items.map((item) => (
              <a className="friendCard" href={item.url} target={item.target} rel={item.rel || 'noopener noreferrer'} key={item.id}>
                <div className="friendCardTop">
                  <FriendAvatar item={item} />
                  <div>
                    <h2>{item.name}</h2>
                    <small>{toolHost(item.url)}</small>
                  </div>
                  <ArrowUpRight className="friendCardGo" size={18} aria-hidden="true" />
                </div>
                <p>{item.description || '这位朋友还没有留下介绍。'}</p>
              </a>
            ))}
          </section>
        ) : (
          <p className="dynamicEmpty">暂无友情链接。</p>
        )}
      </main>
    </PublicShell>
  );
}
function GalleriesScreen({ lightsOn, onToggle, navigate }) {
  const [state, setState] = useState({ loading: true, items: [], error: '' });
  useEffect(() => {
    let active = true;
    contentApi
      .galleries()
      .then((result) => {
        if (!active) return;
        const items = result.data || [];
        if (items.length === 1) {
          navigate(`/photos/${encodeURIComponent(items[0].slug)}`);
          return;
        }
        setState({ loading: false, items, error: '' });
      })
      .catch((error) => active && setState({ loading: false, items: [], error: error.message || '图库加载失败。' }));
    return () => {
      active = false;
    };
  }, [navigate]);
  return (
    <PublicShell lightsOn={lightsOn} onToggle={onToggle} navigate={navigate}>
      <main className="wrap listingPage">
        <header className="listingHead">
          <h1>图库</h1>
          <span>记录值得保存的瞬间。</span>
        </header>
        {state.loading ? (
          <p className="dynamicEmpty">正在加载…</p>
        ) : state.error ? (
          <p className="dynamicError">{state.error}</p>
        ) : (
          <section className="articleList">
            {state.items.map((item) => (
              <button className="listRow" onClick={() => navigate(`/photos/${encodeURIComponent(item.slug)}`)} key={item.id}>
                {item.coverUrl ? (
                  <img src={item.coverUrl} alt="" />
                ) : (
                  <div className="listNoCover">
                    <ImageIcon />
                  </div>
                )}
                <div className="listCopy">
                  <h2>{item.name}</h2>
                  <p>{item.description || `${item.itemCount} 个媒体文件`}</p>
                </div>
              </button>
            ))}
          </section>
        )}
      </main>
    </PublicShell>
  );
}
function GalleryLightbox({ items, index, onClose, onChange, hasMore }) {
  const item = items[index];
  const canGoPrevious = index > 0;
  const canGoNext = index < items.length - 1 || hasMore;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="图片预览"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100,
        display: 'grid',
        gridTemplateColumns: 'auto minmax(0,1fr) auto',
        alignItems: 'center',
        gap: 16,
        padding: 24,
        background: 'rgba(4,12,19,.92)',
      }}
    >
      <button
        onClick={onClose}
        aria-label="关闭预览"
        style={{
          position: 'absolute',
          top: 20,
          right: 24,
          border: 0,
          borderRadius: 8,
          background: 'rgba(255,255,255,.14)',
          color: '#fff',
          fontSize: 28,
          lineHeight: 1,
          padding: '6px 11px',
          cursor: 'pointer',
        }}
      >
        ×
      </button>
      <button
        onClick={() => onChange(index - 1)}
        disabled={!canGoPrevious}
        aria-label="上一张"
        style={{
          border: 0,
          background: 'transparent',
          color: '#fff',
          fontSize: 54,
          cursor: canGoPrevious ? 'pointer' : 'default',
          opacity: canGoPrevious ? 1 : 0.25,
        }}
      >
        ‹
      </button>
      <figure style={{ display: 'grid', justifyItems: 'center', gap: 12, maxWidth: '100%', maxHeight: '100%', margin: 0 }}>
        <img
          src={item.url}
          alt={item.alt || item.name || ''}
          style={{ display: 'block', maxWidth: '100%', maxHeight: 'calc(100vh - 108px)', objectFit: 'contain' }}
        />
        <figcaption style={{ color: '#fff', fontSize: 14 }}>{item.title || item.name || `${index + 1} / ${items.length}`}</figcaption>
      </figure>
      <button
        onClick={() => onChange(index + 1)}
        disabled={!canGoNext}
        aria-label="下一张"
        style={{
          border: 0,
          background: 'transparent',
          color: '#fff',
          fontSize: 54,
          cursor: canGoNext ? 'pointer' : 'default',
          opacity: canGoNext ? 1 : 0.25,
        }}
      >
        ›
      </button>
    </div>
  );
}
function GalleryScreen({ slug, lightsOn, onToggle, navigate }) {
  const [state, setState] = useState({
    loading: true,
    item: null,
    items: [],
    page: 0,
    total: 0,
    hasMore: false,
    loadingMore: false,
    error: '',
  });
  const [lightboxIndex, setLightboxIndex] = useState(null);
  const loadPage = useCallback(
    async (page, append) => {
      setState((value) => ({ ...value, loading: !append, loadingMore: append, error: '' }));
      try {
        const result = await contentApi.gallery(slug, { page, page_size: 24 });
        const nextItems = result.data.items || [];
        const meta = result.meta || {};
        setState((value) => ({
          loading: false,
          item: result.data,
          items: append ? [...value.items, ...nextItems] : nextItems,
          page: Number(meta.page) || page,
          total: Number(meta.total) || nextItems.length,
          hasMore: (Number(meta.page) || page) < (Number(meta.totalPages) || 1),
          loadingMore: false,
          error: '',
        }));
        return nextItems.length;
      } catch (error) {
        setState((value) => ({ ...value, loading: false, loadingMore: false, error: error.message || '图库加载失败。' }));
        return 0;
      }
    },
    [slug],
  );
  useEffect(() => {
    setLightboxIndex(null);
    loadPage(1, false);
  }, [loadPage]);
  const loadMore = useCallback(() => {
    if (state.loading || state.loadingMore || !state.hasMore) return Promise.resolve(0);
    return loadPage(state.page + 1, true);
  }, [loadPage, state.hasMore, state.loading, state.loadingMore, state.page]);
  const loadMoreRef = useRef(null);
  loadMoreRef.current = loadMore;
  const sentinelRef = useRef(null);
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel || !state.hasMore || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMoreRef.current?.();
      },
      { rootMargin: '600px 0px' },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [state.hasMore]);
  const changeLightbox = async (next) => {
    if (next < 0) return;
    if (next < state.items.length) {
      setLightboxIndex(next);
      return;
    }
    if (state.hasMore && (await loadMore()) > 0) setLightboxIndex(next);
  };
  return (
    <PublicShell lightsOn={lightsOn} onToggle={onToggle} navigate={navigate}>
      <main className="photoGalleryPage">
        {state.loading ? (
          <p className="dynamicEmpty">正在加载…</p>
        ) : state.error ? (
          <p className="dynamicError">{state.error}</p>
        ) : (
          <>
            <section className="photoWall" aria-label={`${state.item.name}图片列表`}>
              {state.items.map((item, index) => (
                <button type="button" className="photoTile" onClick={() => setLightboxIndex(index)} key={item.id}>
                  {item.mimeType?.startsWith('image/') ? (
                    <img src={item.url} alt={item.alt || item.title || item.name || ''} loading="lazy" decoding="async" />
                  ) : (
                    <span className="photoTileFallback">
                      <ImageIcon size={32} />
                    </span>
                  )}
                  <b>{item.title || item.name}</b>
                </button>
              ))}
            </section>
            {!state.items.length && <p className="dynamicEmpty">这个图库还没有图片。</p>}
            {state.hasMore && (
              <div className="photoWallSentinel" ref={sentinelRef} aria-live="polite">
                {state.loadingMore ? '正在加载更多图片…' : '继续向下滚动加载更多图片'}
              </div>
            )}
            {lightboxIndex !== null && (
              <GalleryLightbox
                items={state.items}
                index={lightboxIndex}
                hasMore={state.hasMore}
                onClose={() => setLightboxIndex(null)}
                onChange={changeLightbox}
              />
            )}
          </>
        )}
      </main>
    </PublicShell>
  );
}
function App() {
  const [lightsOn, setLightsOn] = useState(false);
  const [location, setLocation] = useState(() => window.location.pathname + window.location.search);
  const [home, setHome] = useState(null);
  const [homeError, setHomeError] = useState('');
  const [site, setSite] = useState(null);
  const navigate = useCallback((to, { replace = false } = {}) => {
    let target = to || '/';
    target = target.replace(/^\/posts\?category=([^&]+)$/, '/categories/$1').replace(/^\/posts\?tag=([^&]+)$/, '/tags/$1');
    if (window.location.pathname + window.location.search !== target)
      window.history[replace ? 'replaceState' : 'pushState']({}, '', target);
    setLocation(target);
    window.scrollTo({ top: 0, behavior: 'auto' });
  }, []);
  useEffect(() => {
    const update = () => setLocation(window.location.pathname + window.location.search);
    window.addEventListener('popstate', update);
    return () => window.removeEventListener('popstate', update);
  }, []);
  useEffect(() => {
    let active = true;
    contentApi
      .home()
      .then((result) => active && setHome(result.data))
      .catch(() => active && setHomeError('首页数据暂时无法加载。'));
    contentApi
      .site()
      .then((result) => active && setSite(result.data))
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!site) return;
    document.title = site.siteName || document.title;
    const description =
      document.querySelector('meta[name="description"]') || Object.assign(document.createElement('meta'), { name: 'description' });
    description.content = site.siteDescription || '';
    if (!description.parentNode) document.head.appendChild(description);
    let robots = document.querySelector('meta[name="robots"]');
    if (site.seoNoindex) {
      robots = robots || Object.assign(document.createElement('meta'), { name: 'robots' });
      robots.content = 'noindex,nofollow';
      if (!robots.parentNode) document.head.appendChild(robots);
    } else if (robots) {
      robots.remove();
    }
    let keywords = document.querySelector('meta[name="keywords"]');
    if (site.siteKeywords) {
      keywords = keywords || Object.assign(document.createElement('meta'), { name: 'keywords' });
      keywords.content = site.siteKeywords;
      if (!keywords.parentNode) document.head.appendChild(keywords);
    } else if (keywords) {
      keywords.remove();
    }
  }, [site]);
  const pathname = location.split('?')[0];
  if (pathname === '/login') return <AuthScreen mode="login" navigate={navigate} location={location} />;
  if (pathname === '/register') return <AuthScreen mode="register" navigate={navigate} location={location} />;
  if (pathname === '/verify-email') return <VerifyEmailScreen navigate={navigate} location={location} />;
  if (pathname === '/studio' || pathname.startsWith('/studio/'))
    return (
      <React.Suspense fallback={<main className="studioLoading">正在加载工作台…</main>}>
        <StudioApp navigate={navigate} location={location} />
      </React.Suspense>
    );
  if (pathname === '/links')
    return <FriendLinksScreen lightsOn={lightsOn} onToggle={() => setLightsOn((value) => !value)} navigate={navigate} />;
  if (pathname === '/tools') return <ToolsScreen lightsOn={lightsOn} onToggle={() => setLightsOn((value) => !value)} navigate={navigate} />;
  if (pathname === '/tags') return <TagsScreen lightsOn={lightsOn} onToggle={() => setLightsOn((value) => !value)} navigate={navigate} />;
  if (pathname === '/photos')
    return <GalleriesScreen lightsOn={lightsOn} onToggle={() => setLightsOn((value) => !value)} navigate={navigate} />;
  if (pathname.startsWith('/photos/'))
    return (
      <GalleryScreen
        slug={decodeURIComponent(pathname.slice('/photos/'.length))}
        lightsOn={lightsOn}
        onToggle={() => setLightsOn((value) => !value)}
        navigate={navigate}
      />
    );
  if (pathname === '/posts' || pathname.startsWith('/categories/') || pathname.startsWith('/tags/'))
    return <PostListScreen lightsOn={lightsOn} onToggle={() => setLightsOn((value) => !value)} navigate={navigate} site={site} />;
  if (pathname.startsWith('/archives/'))
    return (
      <ArticleScreen
        slug={decodeURIComponent(pathname.slice('/archives/'.length))}
        lightsOn={lightsOn}
        onToggle={() => setLightsOn((value) => !value)}
        navigate={navigate}
      />
    );
  if (/^\/[^/]+$/.test(pathname))
    return (
      <PageScreen
        slug={decodeURIComponent(pathname.slice(1))}
        lightsOn={lightsOn}
        onToggle={() => setLightsOn((value) => !value)}
        navigate={navigate}
      />
    );
  return (
    <PublicShell lightsOn={lightsOn} onToggle={() => setLightsOn((value) => !value)} navigate={navigate}>
      <Hero lightsOn={lightsOn} postCount={home?.postCount} siteViewCount={home?.siteViewCount} navigate={navigate} />
      {homeError && <p className="wrap dynamicError">{homeError}</p>}
      <Main home={home} navigate={navigate} />
    </PublicShell>
  );
}
createRoot(document.getElementById('root')).render(<App />);
