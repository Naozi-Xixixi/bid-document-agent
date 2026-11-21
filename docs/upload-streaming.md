# 大文件上传与流式生成

招标文件解析入口默认支持最大 1 GiB 的 DOC、DOCX、PDF。后端通过
BID_MAX_UPLOAD_SIZE_MB 配置上限（单位 MiB，默认 1024）；前端当前同样限制为
1 GiB。部署示例的 Nginx 请求体限制为 1025m，为 multipart 边界预留空间。

## 页面行为

- 上传阶段按浏览器实际传输字节显示百分比、已上传大小和速度，可取消。
- 传输达到 100% 后显示服务器保存与解析状态；100% 不代表 AI 解读完成。
- 重复文件通过网页内对话框选择覆盖、另建项目、合并或取消。
- 目录/大纲及正文逐块显示模型真实输出。完整目录校验通过后才保存；
  图文正文完成后再渲染最终结构、图片和图表。
- 连接异常不自动重新发起生成，避免重复请求。未完成内容不作为完整成果。
- 同一目录任务可以订阅已有进度；同一章节并发生成会被拒绝。
  这些任务协调机制目前用于单个后端进程，多 worker 部署需要共享任务存储。
- 已保存结果可在返回上一步后重新加载；正在传输的文件或生成流会随页面离开而中断，
  已经开始的后台文件解析继续运行。返回项目后可以查看解析状态。

## 信息提取进度

步骤 3 按实际完成的 8 个事项显示进度：定位原文、项目概况、评分办法、
资格要求、商务要求、废标/否决条款、汇总结论、保存结果。百分比表示已完成
事项占比，不表示已消耗或剩余时间；某一项等待模型返回时，百分比保持不变，
已用时间继续更新。可展开列表查看各项状态及重试次数，使用规则回退时会单独标记。

保存成功后才显示 100%。分项进度随当前解析文件持久化，重新进入页面可恢复。
旧任务没有分项记录时显示不定进度，不推测百分比。更新后端前应等待当前解析
或正文生成任务完成，避免重启中断正在进行的模型请求。

## 大 DOCX 的处理

解析正文时直接按 XML 读取段落、标题和表格，不加载 DOCX 内所有图片。
这能减少读取大型图文 Word 的时间与内存；后续 AI 解读耗时仍取决于文档内容和模型服务。

Windows 本地启动前可把 multipart 临时目录设到空间充足的磁盘，例如在项目根目录运行：

    New-Item -ItemType Directory -Force .cache\tmp | Out-Null
    $env:TEMP = (Resolve-Path .cache\tmp).Path
    $env:TMP = $env:TEMP

随后在同一 PowerShell 中进入 backend 并启动 uvicorn。这些环境变量只影响该终端及子进程。

## 离线回归检查

在 backend 目录：

    python -m unittest discover -s tests -p "test_*.py" -v
    python -m unittest discover -s scripts -p test_large_bid_upload.py -v

在 frontend 目录：

    node --test tests/stream-and-upload.test.mjs tests/duplicate-file.test.mjs tests/extraction-progress.test.mjs
    npx tsc --noEmit
    npm run build

测试使用模拟模型输出，不调用付费模型 API。覆盖首段提前到达、UTF-8 分块、
断流、取消、批量部分失败、目录订阅、保存竞争、上传分块与内存、DOCX 表格与标题。
实际云模型输出质量以及完整大文件上传后的 AI 解读还需业务验收。
