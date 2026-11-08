const fieldLabels = {
  title: '标题',
  slug: '地址 slug',
  content: '正文',
  summary: '摘要',
  coverUrl: '封面链接',
  cover_url: '封面链接',
  locale: '语言',
  contentFormat: '正文格式',
  content_format: '正文格式',
  categoryIds: '分类',
  category_ids: '分类',
  tagIds: '标签',
  tag_ids: '标签',
};

export function validationMessage(fields, fallback) {
  if (!Array.isArray(fields?.errors)) return fallback;
  const messages = fields.errors.map((error) => {
    const field = error.loc?.find((part) => Object.hasOwn(fieldLabels, part));
    if (!field) return null;
    let detail = '格式不正确，请检查。';
    if (error.type === 'string_pattern_mismatch' && field === 'slug') {
      detail = '仅支持小写字母、数字、连字符及可选的 .html 后缀。';
    } else if (error.type === 'string_too_long') {
      detail = `最多 ${error.ctx?.max_length} 个字符。`;
    } else if (error.type === 'string_too_short' || error.type === 'missing') {
      detail = '不能为空。';
    } else if (error.type === 'value_error' && ['coverUrl', 'cover_url'].includes(field)) {
      detail = '请填写 http、https 链接或以 / 开头的站内路径。';
    }
    return `${fieldLabels[field]}：${detail}`;
  }).filter(Boolean);
  return [...new Set(messages)].join(' ') || fallback;
}
