# PPT Master Web Console

一个轻量的产品化外壳，用 Flask + SQLite 包装 PPT Master 的本地工作流。

## 启动

```bash
pip install -r requirements.txt
python3 -m webapp.app
```

默认地址：<http://127.0.0.1:5001>

首次注册的用户自动成为 `admin`。生产环境请至少设置：

```bash
export PPT_MASTER_WEB_SECRET="change-this-to-a-long-random-string"
export PPT_MASTER_ALLOW_REGISTRATION=false
```

## AI 生成配置

Web 服务默认使用服务器端 API runner，而不是本机 Codex/Claude CLI：

```bash
export PPT_MASTER_AGENT_RUNNER=api
export PPT_MASTER_LLM_API_KEY="sk-..."
export PPT_MASTER_LLM_BASE_URL="https://your-openai-compatible-endpoint/v1"
export PPT_MASTER_LLM_MODEL="gpt-5.5"
```

也可以复用仓库根目录 `.env` 里的 `OPENAI_API_KEY` / `OPENAI_BASE_URL`。生产部署建议显式设置 `PPT_MASTER_LLM_*`，避免和 `image_gen.py` 使用的 `OPENAI_MODEL=gpt-image-2` 混在一起。

图片提示词精修现在也支持单独配置；不配置时默认回退到 `PPT_MASTER_LLM_*`：

```bash
export PPT_MASTER_IMAGE_PROMPT_REFINEMENT=true
export PPT_MASTER_IMAGE_PROMPT_PROVIDER="openai-compatible"
export PPT_MASTER_IMAGE_PROMPT_API_KEY="sk-..."
export PPT_MASTER_IMAGE_PROMPT_BASE_URL="https://your-openai-compatible-endpoint/v1"
export PPT_MASTER_IMAGE_PROMPT_MODEL="gpt-5.5"
```

这组配置只用于“生图前的提示词润色”，不会影响 SVG / notes / 自修复主流程。

API runner 提供接近 CLI agent 的受限工具能力：

- 读取仓库规则、模板引用和当前项目源材料
- 写入当前项目的 `design_spec.md`、`spec_lock.md`、`svg_output/`、`notes/`
- 运行 `svg_quality_checker.py` 并根据错误自动回修
- 调用 `image_search.py` 下载开放许可图片
- 调用 `image_gen.py` 使用原项目的图片模型配置生成图片

默认图片生成使用 `512px` 低质量档。部分 OpenAI 兼容代理会强制执行最低像素预算，因此实际输出可能仍接近 1K；这里不减少生图数量，成本优化只来自 low quality 档和避免 `2K/4K`：

```bash
export PPT_MASTER_DEFAULT_IMAGE_SIZE=512px
```

图片能力继续复用原项目配置：

```bash
export IMAGE_BACKEND=openai
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://your-openai-compatible-endpoint/v1"
export OPENAI_MODEL="gpt-image-2"

# 可选：提高 Web 搜图质量
export PEXELS_API_KEY="..."
export PIXABAY_API_KEY="..."
```

本地开发仍可切换：

```bash
export PPT_MASTER_AGENT_RUNNER=codex   # 或 claude
```

## 已支持

- 用户注册 / 登录 / 退出
- 用户隔离的项目目录：`projects/web/<user_id>/...`
- 源材料上传与 URL 导入，复用 `project_manager.py import_sources`
- API runner 自动读取源材料，生成 `svg_output/*.svg` / `notes/*.md`
- SVG 检查失败后自动回修
- 原项目图片搜索 / AI 生图工具接入
- 项目结构校验
- SVG 质量检查
- SVG 后处理
- PPTX 导出与下载
- 预览 SVG 页面与下载 PPTX

## 产品边界

当前 Web 服务会把 Web 一键生成映射为 PPT Master 的原始工作流语义：默认确认 Eight Confirmations，调用文本模型生成 SVG/notes，再串行运行 `svg_quality_checker.py`、`finalize_svg.py`、`svg_to_pptx.py`。如果需要人工参与策略确认，可以在产品层补一个审批流。
