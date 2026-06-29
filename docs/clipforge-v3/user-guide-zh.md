# ClipForge 3.0 中文操作指南

1. 打开 `/v3/projects/new` 创建项目，填写产品名称、类别、尺寸、材质、安装方式、工作表面和安全规则。
2. 在项目控制台检查 Product Truth。事实不确定时不要确认，先补充资料。
3. 上传真实产品图，至少提供 `product_identity` 身份锚点图。身份图必须由用户确认。
4. 点击生成 Director Plan，系统会生成镜头合同、模式、预算和参考素材角色。
5. 在 Shot Board 中检查每个镜头：每个镜头只能有一个主要动作和一个主要摄像机运动。
6. 编译 Prompt，打开 Prompt Inspector 检查注入项、压缩记录、linter 结果和 payload preview。
7. 先执行 Draft Preflight，再提交 Draft。Production 生成前必须查看成本、风险和预算。
8. 在 Take Review Studio 中评分、选择 Verdict、填写错误码并选择 Take。
9. 所有必需镜头都有 selected Take 后，执行 Final Assembly。
10. Publish Gate 通过后再配置发布渠道。

当前运行模式：

- Mock Alpha：默认模式，不调用真实 Seedance，不产生付费。用于检查 Product Truth、分镜、Prompt、审核和拼接流程。
- Real API Test：只适合单镜头人工测试。页面必须显示 Provider 为 Ark 且 Real API Enabled，操作员必须看到费用、分辨率、时长、参考素材和幂等 Key 前缀，并完成二次确认。
- Production：当前仍未完成，不应交给外部客户或作为收费产品使用。

真实付费生成注意事项：

- 仅配置 `ARK_API_KEY` 不会触发真实生成。
- 必须同时开启 `V3_VIDEO_PROVIDER=ark` 和 `V3_REAL_API_ENABLED=true`。
- 后端会验证确认 Token，不能只依赖前端按钮。
- 如果状态为 `unknown_submission_state`，不要重复点击生成；需要先确认 Provider 是否已经接收任务，避免重复扣费。
- Mock 视频会标记为 Mock，不应作为真实 Seedance 结果交付。

常见阻塞处理：

- Product Truth 未确认：回到产品事实步骤确认。
- 产品身份图缺失：上传真实产品图并勾选身份锚点。
- Prompt 超过 2000 字符：拆镜头或减少次要动作。
- 连续性依赖未完成：先选择上游镜头 Take。
- 预算超限：降低分辨率、缩短时长或拆分项目。
