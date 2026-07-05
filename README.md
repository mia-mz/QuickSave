# Quicksave — 工作存档点

像游戏存档一样冻结与恢复工作状态:被打断前一键存档(留一句"我正要干嘛"),
回来时一键读档,浏览器标签、终端目录、VS Code 工作区、Claude Code / Codex
会话按差量补回来(已经开着的不动)。

数据全部存在本机 `~/Library/Application Support/Quicksave/`,不上传任何内容。

## 用法

```
qs              # 弹面板选存档/读档
qs save -m "正在改 IPCW 权重" -p PHLLM
qs list
qs load [编号]
qs ui           # 图形面板 http://127.0.0.1:7799
```

`qs` 为 `python3 ~/Projects/quicksave/quicksave.py` 的别名。

## 恢复行为

- 差量恢复:只补"当时开着、现在关了"的标签页和终端目录
- AI 会话按存档时的宿主恢复:当时在 VS Code 内置终端里跑的,就打开对应
  工作区并在新内置终端里 `claude --resume <会话ID>`;在 Terminal 里跑的回
  Terminal。往 VS Code 里自动输入需要给终端授"辅助功能"权限,且仅在确认
  VS Code 位于前台时才会粘贴
- 读档结束给一份实账:补开了多少、跳过了多少、每个会话接在了哪里

## 权限(一次性)

- 自动化:允许终端控制 Chrome / Safari / Terminal / System Events
- 辅助功能:允许终端模拟按键(仅用于往 VS Code 内置终端粘贴恢复命令)
