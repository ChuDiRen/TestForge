/** utils 公用工具层：与业务无关的纯函数 */

/** 仓库展示名：取 url 末段并去掉 .git 后缀（TestForge.git → TestForge） */
export const repoName = (url: string) =>
  decodeURIComponent(String(url).replace(/\/+$/, ''))
    .split('/')
    .pop()
    ?.replace(/\.git$/i, '') || String(url)
