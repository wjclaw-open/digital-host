# 数字主持人 · 小薇

AI 驱动的虚拟数字主持人，支持实时聊天、语音播读（TTS）、3D 头像展示。

## 功能

- AI 实时对话（支持 OpenAI 兼容 API）
- Edge TTS 语音播读（中文）
- 3D 头像动画
- 对话历史持久化
- 飞书等外部消息推送（`/push` 接口）
- Web 页面配置 API 参数

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python server.py
```

### 3. 配置 API

浏览器打开 `http://localhost:8767`，点击右上角 **设置** 按钮，填写：

- **API 地址**：OpenAI 兼容 API 的 Base URL，如 `https://api.openai.com/v1`
- **API Key**：你的 API 密钥
- **模型名称**：如 `gpt-4o`、`deepseek-v3`、`qwen3-...`

配置保存在 `config.json`（不会上传到 GitHub）。

## 外部调用

### 推送消息（飞书等）

```bash
curl -X POST http://localhost:8767/push \
  -H "Content-Type: application/json" \
  -d '{"type":"user","text":"你好"}'
```

### 聊天接口

```bash
curl -X POST http://localhost:8767/chat \
  -H "Content-Type: application/json" \
  -d '{"text":"你好","session_id":"test"}'
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `server.py` | Flask 后端服务 |
| `index.html` | 前端页面（聊天 + 3D 头像） |
| `启动.bat` | Windows 一键启动 |

## License

MIT
