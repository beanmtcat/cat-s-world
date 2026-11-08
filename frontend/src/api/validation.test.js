import assert from 'node:assert/strict';
import test from 'node:test';
import { validationMessage } from './validation.js';

test('reports the slug field and accepted legacy extension', () => {
  assert.equal(validationMessage({ errors: [{ loc: ['body', 'slug'], type: 'string_pattern_mismatch' }] }, '请求参数不合法。'),
    '地址 slug：仅支持小写字母、数字、连字符及可选的 .html 后缀。');
});

test('reports summary limits and invalid cover URLs using field labels', () => {
  assert.equal(validationMessage({ errors: [
    { loc: ['body', 'summary'], type: 'string_too_long', ctx: { max_length: '1000' } },
    { loc: ['body', 'coverUrl'], type: 'value_error' },
  ] }, '请求参数不合法。'), '摘要：最多 1000 个字符。 封面链接：请填写 http、https 链接或以 / 开头的站内路径。');
});

test('labels nested taxonomy failures and avoids echoing submitted values', () => {
  assert.equal(validationMessage({ errors: [{ loc: ['body', 'tagIds', 0], type: 'uuid_parsing', input: 'private-input' }] }, '失败'),
    '标签：格式不正确，请检查。');
});

test('uses the original message when structured validation details are absent or unrelated', () => {
  for (const fields of [undefined, {}, { errors: [] }, { errors: [{ loc: ['body', 'password'], msg: 'secret-input' }] }]) {
    assert.equal(validationMessage(fields, '请求参数不合法。'), '请求参数不合法。');
  }
});
