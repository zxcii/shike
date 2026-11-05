# 本地界面使用的开源组件

这些文件随项目保存在 `static/vendor/`。运行时仅从本地加载，没有 CDN、遥测或外部前端请求。固定版本的官方下载地址、字节数和 SHA-256 见 `static/vendor/manifest.json`。许可证原文随各组件保留。

| 组件 | 本项目使用版本 | 许可 | 用途 | 上游项目 |
| --- | --- | --- | --- | --- |
| jsTree | 3.3.17 | MIT | 多级目录、搜索、键盘导航、分类拖动与层级调整 | https://github.com/vakata/jstree |
| SortableJS | 1.15.7 | MIT | 书架拖动排序；另外提供键盘可点的前移/后移操作 | https://github.com/SortableJS/Sortable |
| jQuery | 3.7.1 | MIT | jsTree 官方依赖 | https://github.com/jquery/jquery |

2026-11-05 查询上游仓库时，jsTree 约 5.2k stars，SortableJS 约 31.2k stars。热度仅用于辅助选择；实际接入依据是组件能力与现有原生 JavaScript / FastAPI 项目兼容，不引入另一个前端框架或完整图书服务。

接入参考 jsTree 官方 JSON 数据与事件示例（https://www.jstree.com/docs/）、dnd/search 插件示例（https://www.jstree.com/plugins/），以及 SortableJS 官方 README 的 handle、draggable、onEnd 用法。应用的书本外观、课程关联和学习流程属于本项目实现；没有复制其他阅读器或商业产品界面源码。

应用使用 Python 标准库 SQLite 管理书本、分类和课程位置，沿用现有题集 JSON 记录保存作答及反馈。SQLite 支持事务、外键与参数化 SQL，避免另外实现这些基础能力。

## 模型供应商 SDK

- **LiteLLM Core 1.108.0**：直接使用 [BerriAI/litellm](https://github.com/BerriAI/litellm) 的官方轻量 SDK 发行包 `litellm-core`，通过 `litellm.acompletion` 复用供应商适配、认证、消息/响应转换及错误类型。2026-11-05 查询仓库约 62.8k stars。
- 许可证：使用的 SDK 属于 MIT 许可部分；本项目没有包含仓库 `enterprise/` 的代码或启动网关企业服务。SDK 与依赖保存在 `.venv/`，MIT 原文由已安装发行包携带；依赖固定版本记录在 `requirements.lock.txt`。
- 接入依据：[Python SDK](https://docs.litellm.ai/docs/python_sdk)、[供应商目录](https://docs.litellm.ai/docs/providers)、[OpenAI 兼容接口](https://docs.litellm.ai/docs/providers/openai_compatible)、[离线目录设置](https://docs.litellm.ai/docs/proxy/outbound_requests)。
- 应用只实现连接设置的本地保存、课程上下文、任务生命周期、题集协议校验、结果归档和界面。没有逐家手写供应商调用，也没有从其他项目复制密钥或配置文件。
