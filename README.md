# AI 小助理

桌面版个人 AI 助手：聊天界面 + 记忆库工作台，深紫霓虹的二次元风格，可打包发给别人用。

## 跑起来

```bat
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
start.bat
```

首次启动会走向导：选记忆库目录（没有就让程序建一个空的）+ 填自己的 DeepSeek API Key。

## 打包

```bat
build.bat                                    :: 生成 dist\AI小助理\AI小助理.exe
powershell -ExecutionPolicy Bypass -File package_release.ps1   :: 生成可发放的 release\*.zip
```

发布脚本会自动剥掉 `data/`（配置、密钥、会话、备份），这一点不要改。

## 自检

```bat
python tests\smoke_test.py       :: 记忆库读写、检索、渲染、情绪解析（不联网）
python tests\first_run_test.py   :: 陌生电脑首次启动向导、密钥不外泄（不联网）
python tests\ui_selftest.py      :: 界面是否卡死、树有没有渲染出来（不联网）
python tests\ui_chat_test.py     :: 端到端真发一条消息（调用一次 DeepSeek）
```

## 结构

| 路径 | 作用 |
|---|---|
| `main.py` | 窗口入口 + 前端可调用的桥接接口 |
| `core/llm.py` | DeepSeek 流式客户端、系统提示词、情绪表 |
| `core/memory.py` | 记忆库扫描、检索、读写、备份、回收站、Markdown 渲染 |
| `core/store.py` | 配置与会话的本地持久化 |
| `ui/` | 界面（无框架、无 CDN，改配色只动 `style.css` 顶部变量） |
| `assets/make_icon.py` | 生成应用图标 |

## 两条硬约束

1. **不要给桥接对象加公开属性**。`Api` 上所有状态必须以下划线开头，否则 pywebview 生成
   桥接时会递归遍历它、走进 WinForms 无障碍树，直接卡死界面。细节见记忆库知识卡
   「编程-pywebview-js_api公开属性导致界面卡死」。
2. **发布包不能带 `data/`**。密钥明文存在里面。

## 能力边界

只做两件事：和用户对话、读写记忆库。不联网、不执行命令、不碰记忆库以外的文件。
