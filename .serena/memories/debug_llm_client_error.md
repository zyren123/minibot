## 问题诊断

**错误**: `'OpenAI' object has no attribute 'messages'`

**原因**: 标准 OpenAI SDK 的 API 调用方式是 `client.chat.completions.create()`，而不是 `client.messages.create()`。代码使用了错误的 API。

**修复方案**:
1. 将 `self._client.messages.create()` 改为 `self._client.chat.completions.create()`
2. 将 `messages` 参数中的 `{"role": "content"}` 格式改为 OpenAI chat API 的格式 (system 在 messages 数组中)
3. 添加详细日志帮助诊断

## 修改文件
- `src/minibot/core/client.py` - LLMClient 类