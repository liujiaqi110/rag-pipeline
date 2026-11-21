import { describe, expect, it } from 'vitest'
import { prepareAnswer, renderAnswer } from '@/utils/markdown'
import { parseSse } from '@/api/sse'
import { filterFiles } from '@/utils/files'
import { buildDeptTree } from '@/utils/tree'
import { statusNotice } from '@/utils/answer'
import { allowedFile } from '@/utils/format'

const cites = [{ chunk_id: 'aaaaaa111111', source: '1234abcd_runbook.md' }, { chunk_id: 'bbbbbb222222', source: 'x.md' }]

describe('answer rendering', () => {
  it('maps validated citations to numbered markers and strips the 引用 line', () => {
    const t = prepareAnswer('发布前要测试 [aaaaaa111111]，并评审 [bbbbbb222222]。\n引用：aaaaaa111111, bbbbbb222222', cites)
    expect(t).not.toContain('引用')
    expect(t).toContain('@@CITE1@@')
    expect(t).toContain('@@CITE2@@')
    const html = renderAnswer('发布前要测试 [aaaaaa111111]。', cites)
    expect(html).toContain('<sup class="kb-cite" data-cite="1">1</sup>')
  })
  it('drops any 12-hex id that is not a validated citation', () => {
    const html = renderAnswer('见 [cccccc333333] 和 [aaaaaa111111]', cites)
    expect(html).not.toContain('cccccc333333')
    expect(html).toContain('data-cite="1"')
  })
  it('never renders raw HTML from the model (XSS)', () => {
    const html = renderAnswer('<img src=x onerror=alert(1)><script>alert(2)</script>**粗体** [x](javascript:alert(3))', [])
    const doc = new DOMParser().parseFromString(html, 'text/html')
    expect(doc.querySelectorAll('img, script, iframe').length).toBe(0)          // markup is escaped, never parsed
    expect([...doc.querySelectorAll('*')].some((el) => [...el.attributes].some((a) => a.name.startsWith('on')))).toBe(false)
    expect([...doc.querySelectorAll('a')].some((a) => /^javascript:/i.test(a.getAttribute('href') || ''))).toBe(false)
    expect(html).toContain('<strong>粗体</strong>')
  })
  it('renders code blocks with highlighting', () => {
    const html = renderAnswer('```python\nprint("hi")\n```', [])
    expect(html).toContain('<pre class="hljs"><code>')
  })
})

describe('SSE parser', () => {
  it('handles events split across network chunks', () => {
    const a = parseSse('event: delta\ndata: {"text":"你')
    expect(a.events).toHaveLength(0)
    const b = parseSse(a.rest + '好"}\n\nevent: final\ndata: {"answer":"你好","citations":[]}\n\nevent: de')
    expect(b.events.map((e) => e.event)).toEqual(['delta', 'final'])
    expect(b.events[0].data.text).toBe('你好')
    expect(b.rest).toBe('event: de')
  })
})

describe('files & tree & notices', () => {
  it('filters unsupported formats', () => {
    const f = (n: string) => new File(['x'], n)
    const r = filterFiles([f('a.md'), f('b.PDF'), f('c.docx'), f('d.txt'), f('e.exe'), f('f.doc')])
    expect(r.files.map((x) => x.name)).toEqual(['a.md', 'b.PDF', 'c.docx', 'd.txt'])
    expect(r.skipped).toEqual(['e.exe', 'f.doc'])
    expect(allowedFile('x.markdown')).toBe(true)
  })
  it('builds the department tree and keeps orphans as roots', () => {
    const t = buildDeptTree([
      { id: 1, name: 'rnd', description: '', parent: null },
      { id: 2, name: 'rnd_platform', description: '', parent: 'rnd' },
      { id: 3, name: 'orphan', description: '', parent: 'missing' },
    ], [{ id: 1, username: 'u', display_name: '', roles: [], departments: ['rnd_platform'], clearance: 1, active: true, is_admin: false }])
    expect(t.map((n) => n.name).sort()).toEqual(['orphan', 'rnd'])
    expect(t.find((n) => n.name === 'rnd')!.children[0].count).toBe(1)
  })
  it('explains no-answer and permission-limited statuses', () => {
    expect(statusNotice('refused')!.type).toBe('info')
    expect(statusNotice('access_denied')!.type).toBe('warning')
    expect(statusNotice('ok')).toBeNull()
  })
})
