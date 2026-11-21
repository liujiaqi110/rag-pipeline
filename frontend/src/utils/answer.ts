/** User-facing hint for non-ok answer statuses (no-answer and permission-limited cases). */
export function statusNotice(status: string): { type: 'info' | 'warning' | 'error'; text: string } | null {
  switch (status) {
    case 'refused':
    case 'insufficient_evidence':
      return { type: 'info', text: '你有权访问的资料中没有找到能回答该问题的内容。' }
    case 'insufficient_scoped_evidence':
      return { type: 'info', text: '检索结果中没有所指定文档的内容（或你无权访问该文档）。' }
    case 'partial_answer':
      return { type: 'info', text: '资料只能回答问题的一部分。' }
    case 'access_denied':
      return { type: 'warning', text: '你当前没有该知识库中任何文档的访问权限，或权限配置异常。' }
    case 'blocked':
      return { type: 'error', text: '该请求因权限校验未通过而被终止。' }
    case 'retrieval_error':
    case 'generation_error':
      return { type: 'error', text: '检索或生成服务暂时不可用，请稍后重试。' }
    default:
      return null
  }
}
