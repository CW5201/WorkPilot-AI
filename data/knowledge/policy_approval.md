# 企业制度：高风险操作审批规范

- 邮件对外发送（email.send）：需主管确认收件人、主题、正文与附件。
- 日程变更（calendar.update）：涉及他人日程需经对方确认。
- 文件覆盖写入（file.write / spreadsheet.write）：需明确路径，避免覆盖既有交付物。
- 所有高风险操作在 WAITING_APPROVAL 状态挂起，前端展示风险等级与操作细节，用户确认后才执行。
- 审批超时（默认 300s）自动进入 HUMAN_HANDOFF。
