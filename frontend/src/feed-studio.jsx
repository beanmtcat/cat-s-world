import React, { useEffect, useState } from 'react';
import { Check, ChevronLeft, ChevronRight, Pencil, Plus, RefreshCw, Search, Trash2, X } from 'lucide-react';
import { studioApi } from './api/client';

function localInputTime(value) {
  const date = value ? new Date(value) : new Date();
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function emptyForm() {
  return { title: '', kicker: '', category: '', bodyMd: '', publishedAt: localInputTime() };
}

export default function FeedStudio() {
  const [items, setItems] = useState([]);
  const [comments, setComments] = useState([]);
  const [meta, setMeta] = useState({ page: 1, total: 0, totalPages: 0 });
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [query, setQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const load = async (nextPage = page) => {
    setLoading(true);
    setError('');
    try {
      const [posts, discussion] = await Promise.all([studioApi.feedPosts({ page: nextPage, pageSize: 20 }), studioApi.feedComments()]);
      setItems(posts.data || []);
      setMeta(posts.meta || {});
      setComments(discussion.data || []);
      setPage(nextPage);
    } catch (reason) {
      setError(reason.message || '无法读取动态。');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(1); }, []);
  const edit = (item) => {
    setEditing(item || {});
    setForm(item ? {
      title: item.title,
      kicker: item.kicker || '',
      category: item.category,
      bodyMd: item.bodyMd,
      publishedAt: localInputTime(item.publishedAt),
    } : emptyForm());
  };
  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      const body = { ...form, publishedAt: new Date(form.publishedAt).toISOString() };
      if (editing?.id) await studioApi.updateFeedPost(editing.id, body);
      else await studioApi.createFeedPost(body);
      setEditing(null);
      await load(1);
    } catch (reason) {
      setError(reason.message || '保存动态失败。');
    } finally {
      setSaving(false);
    }
  };
  const remove = async (item) => {
    if (!window.confirm(`确定删除「${item.title}」？删除后前台不再显示。`)) return;
    try {
      await studioApi.deleteFeedPost(item.id);
      await load(page);
    } catch (reason) {
      setError(reason.message || '删除失败。');
    }
  };
  const moderate = async (item, status) => {
    try {
      const result = await studioApi.moderateFeedComment(item.id, status);
      setComments((current) => current.map((entry) => entry.id === item.id ? result.data : entry));
    } catch (reason) {
      setError(reason.message || '审核失败。');
    }
  };
  const visibleItems = items.filter((item) => (
    (!query || `${item.title} ${item.kicker || ''} ${item.category}`.toLowerCase().includes(query.toLowerCase()))
    && (!categoryFilter || item.category === categoryFilter)
    && (!sourceFilter || item.source === sourceFilter)
  ));
  const categories = [...new Set(items.map((item) => item.category))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  return <div className="feedStudio">
    <div className="studioHeading adminListHeading"><div><p>内容管理</p><h1>动态 <small>{meta.total} 条</small></h1></div>
      <div className="feedStudioHeadingActions"><button className="secondaryButton" onClick={() => load(page)}><RefreshCw size={16} />刷新</button>
        <button className="primaryButton" onClick={() => edit(null)}><Plus size={17} />新增动态</button></div>
    </div>
    {error && <p className="feedStudioError">{error}</p>}
    <section className="adminDirectory feedStudioDirectory">
      <div className="adminListToolbar">
        <div className="adminListSearch"><Search size={16} /><input aria-label="搜索当前页动态" placeholder="搜索当前页动态" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
        <div className="adminListFilters">
          <label>分类 <select value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option value="">全部</option>{categories.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
          <label>来源 <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="">全部</option><option value="api">API 推送</option><option value="studio">后台发布</option></select></label>
        </div>
      </div>
      {loading ? <p className="feedStudioEmpty">正在加载动态…</p> : visibleItems.length ? <div className="adminListRows feedStudioRows">
        {visibleItems.map((item) => <article key={item.id}>
          <div className="adminListPrimary"><b>{item.title}</b><span>{item.kicker || item.category}</span></div>
          <div className="adminListActions"><span className="adminTypePill tag">{item.category}</span><span className="adminTypePill">{item.source === 'api' ? 'API 推送' : '后台发布'}</span><time>{new Date(item.publishedAt).toLocaleString('zh-CN')}</time><button onClick={() => edit(item)}><Pencil size={15} />编辑</button><button className="dangerIcon" onClick={() => remove(item)}><Trash2 size={15} />删除</button></div>
        </article>)}
      </div> : <p className="feedStudioEmpty">{items.length ? '当前筛选下没有动态。' : '暂无动态，可以手动新增或通过 API 推送。'}</p>}
      <footer className="adminListFooter"><span>共 {meta.total || 0} 条 · 当前页 {visibleItems.length} 条</span><nav><button disabled={page <= 1 || loading} aria-label="上一页" onClick={() => load(page - 1)}><ChevronLeft size={16} /></button><button disabled={page >= (meta.totalPages || 0) || loading} aria-label="下一页" onClick={() => load(page + 1)}><ChevronRight size={16} /></button><span>{page} / {Math.max(1, meta.totalPages || 0)}</span></nav></footer>
    </section>
    <div className="feedStudioSectionTitle"><h2>动态讨论</h2><span>{comments.filter((item) => item.status === 'pending').length} 条待审核</span></div>
    <section className="adminDirectory feedStudioDirectory">
      {comments.length ? <div className="adminListRows feedStudioRows feedStudioCommentRows">{comments.map((item) => <article key={item.id}>
        <div className="adminListPrimary"><b>{item.authorName}</b><span>{item.body}</span></div>
        <div className="adminListActions"><span className={`adminTypePill ${item.status === 'approved' ? 'healthy' : ''}`}>{item.status === 'pending' ? '待审核' : item.status === 'approved' ? '已通过' : '已拒绝'}</span><time>{new Date(item.ctime).toLocaleString('zh-CN')}</time><button disabled={item.status === 'approved'} onClick={() => moderate(item, 'approved')}><Check size={15} />通过</button><button disabled={item.status === 'rejected'} onClick={() => moderate(item, 'rejected')}><X size={15} />拒绝</button></div>
      </article>)}</div> : <p className="feedStudioEmpty">暂无讨论。</p>}
    </section>
    {editing && <div className="modalScrim" role="presentation" onMouseDown={() => setEditing(null)}><form className="modalCard feedStudioModal" role="dialog" aria-modal="true" aria-label={editing.id ? '编辑动态' : '新增动态'} onMouseDown={(event) => event.stopPropagation()} onSubmit={save}>
      <header><h2>{editing.id ? '编辑动态' : '新增动态'}</h2><button type="button" aria-label="关闭" onClick={() => setEditing(null)}><X size={20} /></button></header>
      <div className="modalBody feedStudioModalBody">
        <label className="studioFormField">标题<input required maxLength={300} value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></label>
        <label className="studioFormField">副标题<input maxLength={200} value={form.kicker} onChange={(event) => setForm({ ...form, kicker: event.target.value })} /></label>
        <div className="feedStudioEditorRow"><label className="studioFormField">分类<input required maxLength={80} value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })} /></label>
          <label className="studioFormField">发布时间<input required type="datetime-local" value={form.publishedAt} onChange={(event) => setForm({ ...form, publishedAt: event.target.value })} /></label></div>
        <label className="studioFormField">正文 Markdown<textarea required rows={12} maxLength={100000} value={form.bodyMd} onChange={(event) => setForm({ ...form, bodyMd: event.target.value })} placeholder="可在正文中使用 ![说明](https://...) 添加图片" /></label>
      </div>
      <footer><button type="button" className="secondaryButton" onClick={() => setEditing(null)}>取消</button><button type="submit" className="primaryButton" disabled={saving}>{saving ? '保存中…' : '保存动态'}</button></footer>
    </form></div>}
  </div>;
}
