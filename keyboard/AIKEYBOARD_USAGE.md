# AiKeyboard 使用文档

## 简介

AiKeyboard.exe 是项目内置的虚拟键盘工具，用于绕过网页密码框的防粘贴机制，模拟真实键盘输入。

---

## 命令列表

### 1. check — 检查安装状态

```bash
AiKeyboard.exe check
```

### 2. input — 输入字符串（常用）

用于输入密码、PIN 等连续字符。

```bash
AiKeyboard.exe input -k "要输入的文本"
```

**示例：**
```bash
AiKeyboard.exe input -k "bobo68029838"
AiKeyboard.exe input -k "68029838"
```

**Python 调用：**
```python
from common.keyboard_helper import input_with_aikeyboard
input_with_aikeyboard("bobo68029838")
```

---

### 3. send — 发送单个按键

用于发送回车、空格、Tab 等单个按键。

```bash
AiKeyboard.exe send -k <按键名称>
```

#### 支持的按键格式

| 按键 | 正确写法 | 错误写法 |
|------|---------|---------|
| 回车 | `Enter` | ~~ENTER~~ / ~~enter~~ |
| 空格 | `Space` | ~~SPACE~~ / ~~space~~ |
| Tab | `Tab` | ~~TAB~~ / ~~tab~~ |
| 字母 A | `A` | ~~a~~ |
| 字母 B | `B` | ~~b~~ |
| 数字 1 | `1` | — |

**规律：**
- **字母**：必须用大写（`A` `B` `C`）
- **特殊键**：首字母大写（`Enter` `Space` `Tab`）
- **数字**：直接写

**示例：**
```bash
AiKeyboard.exe send -k Enter      # 回车
AiKeyboard.exe send -k Space      # 空格
AiKeyboard.exe send -k Tab        # Tab
```

**Python 调用：**
```python
from common.keyboard_helper import send_with_aikeyboard
send_with_aikeyboard("Enter")   # 回车
send_with_aikeyboard("Space")   # 空格
send_with_aikeyboard("Tab")     # Tab
```

---

## 完整示例：UKey 密码输入 + 回车

```python
from common.keyboard_helper import input_with_aikeyboard, send_with_aikeyboard
import time

# 输入 PIN
input_with_aikeyboard("68029838")

# 等待一下
time.sleep(0.5)

# 按回车确认
send_with_aikeyboard("Enter")
```

---

## 注意事项

1. **input 模式**会自动处理输入法切换（非英文输入法会按 Shift 切换）
2. **send 模式**不会切换输入法，确保当前是英文状态
3. 两种模式调用前都建议 `time.sleep(0.5)` 确保目标输入框已激活
4. 如果返回码 `-1`（4294967295），说明按键名称格式错误，检查大小写

---

## 返回码说明

| 返回码 | 含义 |
|--------|------|
| 0 | 成功 |
| -1 (4294967295) | 失败，通常是按键名称格式错误 |
