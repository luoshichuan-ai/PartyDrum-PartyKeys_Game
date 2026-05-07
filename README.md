# 合奏 · 双人节奏游戏

浏览器节奏游戏，左边打鼓、右边弹琴，两个人同时玩一首歌。当前收录曲目：**孙燕姿《不为谁而作的歌》**。

---

## 快速开始

```bash
py -3 server.py
```

打开 Chrome（需要 Web MIDI + SysEx 权限）：

```
http://localhost:8766/index.html
```

> 必须用 Chrome / Edge，Firefox 不支持 Web MIDI API。

---

## 文件结构

```
drum_keyboard/
├── index.html          主游戏（全部逻辑在一个文件里）
├── server.py           本地 HTTP 服务器（端口 8766）
├── song.mp3            背景音乐（Tone.Player 同步播放）
├── song.json           灵动音格式谱面（和弦 + 旋律/歌词原始数据）
├── song.mid            对应 MIDI 文件（备用）
├── convert_song.py     谱面转换脚本（song.json → index.html 内嵌数据）
├── chart.json          旧版大风吹谱面（保留备用）
├── LED_PROTOCOL.md     PartyKeys LED SysEx 协议文档
└── midi-test.html      MIDI 设备调试页面
```

---

## 游戏玩法

| 玩家 | 区域 | 操作 | 目标 |
|------|------|------|------|
| 鼓手 | 左半屏 | 键盘 A / S / K / L | 音符落到判定线时按对应键 |
| 键盘手 | 右半屏 | PartyKeys 36 键琴 | LED 亮起时，在 300ms 内按下和弦所有音 |

**判定等级**

- 🥇 PERFECT — 误差 ≤ 60ms（鼓）/ 80ms（和弦首键）→ 300 × combo 分
- 🥈 GOOD    — 误差 ≤ 120ms → 100 × combo 分
- ❌ MISS    — 超时自动判 MISS，连击归零

**暂停**：点 HUD 右侧 `⏸ PAUSE` 按钮，或按 `P` / `ESC`。

---

## 架构概览

### 渲染循环

```
requestAnimationFrame(tick)
  ├── drumAutoMiss()        自动判 MISS（带指针跳过已处理音符）
  ├── chordAutoMiss()       和弦超时判 MISS
  ├── chordLEDScheduler()   提前 200ms 点亮 PartyKeys LED
  ├── checkEnd()            检测歌曲结束
  ├── updateLyrics(now)     更新歌词索引
  └── draw()
        ├── 清空画布
        ├── 画分隔线（先画，被歌词白条盖住顶部）
        ├── drawDrums()     4 条鼓点轨道 + 落下音符圆圈
        ├── drawKeys()      和弦音符圆圈（对准琴键 X 坐标）+ 钢琴键盘
        ├── drawLyrics()    全宽歌词栏（56px，白底，覆盖分隔线顶部）
        └── 进度条 / 暂停蒙层
```

`updateHUD()`（DOM 写入）每 100ms 执行一次，不在 rAF 里。

### 音频

- **背景 MP3**：`Tone.Player('song.mp3').sync().start(0)`，跟随 `Tone.Transport` 同步
- **鼓声音效**：`Tone.MembraneSynth` / `MetalSynth` / `NoiseSynth`（本地合成）
- **钢琴音色**：Salamander 钢琴采样（从 tonejs.github.io CDN 加载）
- 所有时间坐标统一用 `Tone.Transport.seconds`

### 钢琴键盘布局

36 键 C3–B5（MIDI 48–83），白键 21 根，黑键 15 根。

```javascript
// 每个半音距离八度起点的白键宽度倍数
const KEY_X_OFF = [0.5, 1.0, 1.5, 2.0, 2.5, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5];
//                 C    C#   D    D#   E    F    F#   G    G#   A    A#   B

function keyXCenter(midi) {
  const wkw = HW() / 21;           // 白键宽度
  const rel = midi - 48;
  return HW() + (Math.floor(rel/12)*7 + KEY_X_OFF[rel%12]) * wkw;
}
```

和弦音符圆圈的 X 坐标直接用 `keyXCenter(midi)`，落点精确对准键盘。

### HUD 分隔线对齐

HUD 竖线用 CSS `::after` 伪元素 + `transform: translateX(-50%)` 居中；  
Canvas 竖线用 `ctx.lineWidth=3; ctx.moveTo(hw, 0)` 以坐标为中心描边。  
两者都以 50% 为中轴，像素完全对齐。

---

## 换歌流程

1. 把新歌的 `song.mp3` 和 `song.json`（灵动音格式）放进项目目录
2. 运行转换脚本：

```bash
py -3 convert_song.py
```

脚本自动完成：
- 解析灵动音 JSON（`beat → seconds` 换算、UTF-8 编码修复）
- 提取和弦（MIDI 音符，C3–B5 范围内自动换位）
- 生成 4/4 鼓点 pattern（kick / snare / hihat）
- 提取歌词（38 行精确时间戳）
- 将 `CHART_DATA`、`LYRICS` 常量写入 `index.html`
- 更新标题

### 灵动音 JSON 格式说明

```
tempo[]        节拍 BPM 分段列表，{time: 第几拍, tempo: BPM}
chord[]        和弦列表，{time: 第几拍, figure: 名称, pitches: [音级 0-11]}
melodies[0].notes[]  旋律音符，{start, end, text}
               text 以 '#' 开头的表示新句首
```

**Beat → Seconds 换算**（分段积分）：

```python
def beat_to_sec(beat):
    t = 0.0
    for i, seg in enumerate(tempo_segs):
        b0, bpm = seg['time'], seg['tempo']
        b1 = tempo_segs[i+1]['time'] if i+1 < len(tempo_segs) else float('inf')
        if beat < b0: break
        t += min(beat, b1) - b0) * 60.0 / bpm
        if beat < b1: break
    return t
```

**编码修复**（灵动音文件有时是 UTF-8 被当 latin-1 读入）：

```python
def fix_text(t):
    try: return t.encode('latin-1').decode('utf-8')
    except: return t
```

---

## PartyKeys LED 控制

键盘通过 Web MIDI SysEx 控制 LED。核心命令：

```javascript
// 1. 进入 LED 模式（必须最先发送）
output.send([0xF0, 0x05, 0x30, 0x7F, 0x7F, 0x20, 0x00, 0x0F, 0x01, 0xF7]);

// 2. 设置指定按键颜色（CMD 15）
// R/G/B 各拆为 [high=floor(v/128), low=v%128] 两字节（MIDI 7-bit 安全）
function buildLEDMsg(keys, [r, g, b]) {
  const enc = v => [Math.floor(v/128), v%128];
  return [0xF0,0x05,0x30,0x7F,0x7F,0x20,0x00,0x15,
    0x01, ...enc(r), ...enc(g), ...enc(b), keys.length, ...keys, 0xF7];
}

// 3. 全部熄灭
output.send([0xF0, 0x05, 0x30, 0x7F, 0x7F, 0x20, 0x00, 0x71, 0x00, 0xF7]);
```

- 键盘索引 0–35 对应 MIDI 48–83（C3–B5）
- LED 点亮比和弦时间提前 200ms（`LED_ADVANCE_MS`）
- 硬件延迟约 150–250ms，详见 `LED_PROTOCOL.md`

---

## 性能注意事项

| 问题 | 解决方案 |
|------|---------|
| 每帧 `Math.max(...918元素)` | `loadChart` 时缓存 `lastNoteTime` |
| DOM 写入 60fps | `updateHUD` 改为 `setInterval 100ms` |
| 鼓点遍历 918 个 | `drumAutoMiss` 加指针，跳过已处理音符 |
| 歌词挡分隔线 | 分隔线先画，歌词白底矩形后画覆盖顶部 |

---

## 依赖

| 依赖 | 用途 |
|------|------|
| [Tone.js v14](https://tonejs.github.io/) | Transport 时钟、合成器、MP3 播放 |
| [Salamander Piano](https://github.com/gleitz/midi-js-soundfonts) | 钢琴音色采样（CDN 加载） |
| Web MIDI API | PartyKeys 设备输入 + LED SysEx |
| Python 3 | 本地服务器、谱面转换脚本 |

无其他构建工具，单文件运行。
