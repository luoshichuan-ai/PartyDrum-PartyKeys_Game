#!/usr/bin/env python3
"""Convert song.json (灵动音 format) → game chart data and patch index.html"""
import json, sys, re, math
sys.stdout.reconfigure(encoding='utf-8')

# ── helpers ──────────────────────────────────────────────────────────────────
def fix_text(t):
    """Fix UTF-8 encoded as latin-1"""
    try: return t.encode('latin-1').decode('utf-8')
    except: return t

with open(r'D:\drum_keyboard\song.json', encoding='utf-8') as f:
    raw = json.load(f)

# Try fixing encoding on all text fields
def fix_obj(obj):
    if isinstance(obj, dict):
        return {k: fix_obj(v) for k,v in obj.items()}
    if isinstance(obj, list):
        return [fix_obj(v) for v in obj]
    if isinstance(obj, str):
        return fix_text(obj)
    return obj

song = fix_obj(raw)

# ── Beat → seconds ────────────────────────────────────────────────────────────
tempo_segs = song['tempo']  # list of {time: beat, tempo: BPM}

def beat_to_sec(beat):
    """Convert beat number to seconds using piecewise tempo segments."""
    t_sec = 0.0
    prev_beat = 0.0
    for i, seg in enumerate(tempo_segs):
        seg_start = seg['time']
        seg_bpm   = seg['tempo']
        next_beat = tempo_segs[i+1]['time'] if i+1 < len(tempo_segs) else float('inf')

        if beat <= seg_start:
            break

        # How many beats are covered in this segment?
        covered = min(beat, next_beat) - max(prev_beat, seg_start)
        if covered > 0:
            t_sec += covered * 60.0 / seg_bpm

        prev_beat = seg_start
        if beat <= next_beat:
            break
    return t_sec

# Actually, the standard formula: accumulate time for each segment boundary
def beat_to_sec_v2(beat):
    t = 0.0
    segs = tempo_segs
    for i in range(len(segs)):
        b0 = segs[i]['time']
        bpm = segs[i]['tempo']
        b1 = segs[i+1]['time'] if i+1 < len(segs) else float('inf')

        if beat < b0:
            break

        # Beats consumed in this segment
        beats_in_seg = min(beat, b1) - b0
        if beats_in_seg > 0:
            t += beats_in_seg * 60.0 / bpm

        if beat < b1:
            break
    return t

# Test
print(f"beat 0  = {beat_to_sec_v2(0):.3f}s  (expect 0)")
print(f"beat 4  = {beat_to_sec_v2(4):.3f}s")
print(f"beat 36 = {beat_to_sec_v2(36):.3f}s  (expect ~34.7)")
print(f"beat 88 = {beat_to_sec_v2(88):.3f}s  (expect ~77.7)")
print(f"beat 312= {beat_to_sec_v2(312):.3f}s (expect ~263)")

# ── Extract LYRICS ────────────────────────────────────────────────────────────
lyrics = []
melodies = song.get('melodies', [{}])[0].get('notes', [])

sentence_start = None
sentence_text = []

for note in melodies:
    text = note.get('text', '')
    if text.startswith('#'):
        # New sentence: flush previous
        if sentence_text:
            lyrics.append({'t': sentence_start, 'text': ''.join(sentence_text)})
        sentence_start = beat_to_sec_v2(note['start'])
        sentence_text = [text[1:]]  # strip #
    else:
        if sentence_text is not None:
            sentence_text.append(text)

# Flush last sentence
if sentence_text:
    lyrics.append({'t': sentence_start, 'text': ''.join(sentence_text)})

print(f"\nLyrics: {len(lyrics)} lines")
for l in lyrics:
    print(f"  {l['t']:6.2f}s: {l['text']}")

# ── Extract CHORDS ────────────────────────────────────────────────────────────
# chord: {beat, figure, pitches: [pitch_class 0-11]}
# We need MIDI notes. Place pitches in octave 4/5 (48-83 range for PartyKeys 36-key C3-B5)
# Root in oct 4 (midi 48-59), rest up from there

def pitch_class_to_midi(pc, ref_midi):
    """Get nearest midi note >= ref_midi with this pitch class."""
    base = (ref_midi // 12) * 12 + pc
    if base < ref_midi: base += 12
    return base

def build_chord_midi(pitches):
    """Build MIDI notes from pitch classes, voicing in 48-83 range."""
    if not pitches:
        return []
    root_pc = pitches[0]
    root = pitch_class_to_midi(root_pc, 48)  # root in octave 4
    notes = [root]
    prev = root
    for pc in pitches[1:]:
        n = pitch_class_to_midi(pc, prev + 1)  # always above previous
        if n > 83: n -= 12  # keep in range
        notes.append(n)
        prev = n
    return notes

raw_chords = song.get('chord', [])
chords_data = []
for c in raw_chords:
    t = beat_to_sec_v2(c['time'])
    pitches = c.get('pitches', [])
    if not pitches:
        continue
    midi_notes = build_chord_midi(pitches)
    chords_data.append({'t': round(t, 4), 'notes': midi_notes})

# Filter chords too close together (< 0.5s apart), keep first
filtered_chords = []
for c in chords_data:
    if filtered_chords and c['t'] - filtered_chords[-1]['t'] < 0.5:
        continue
    filtered_chords.append(c)

print(f"\nChords: {len(filtered_chords)} (from {len(chords_data)} raw)")

# ── Generate DRUM PATTERN ─────────────────────────────────────────────────────
# Basic 4/4 pattern based on song structure
# Lanes: 0=A(B1/kick), 1=S(C2/bass-drum), 2=K(D2/snare), 3=L(C#2/hihat)
# Pattern per bar: beat1=kick(1), beat2=hihat(3), beat3=snare(2), beat4=hihat(3)
# + 8th note hihats on off-beats
# Only add drums during actual song (skip intro silence)

# Find song start from first lyric
song_start_beat = 36  # first real lyric section
song_end_beat_approx = 310

KICK  = 1  # lane index (C2 = bass drum feel)
SNARE = 2  # lane index (D2)
HIHAT = 3  # lane index (C#2)
ALT_KICK = 0  # lane index (B1 = alternate kick)

drum_notes = []

# Generate from beat 4 (first chord, skip silent intro) to end, bar by bar (4 beats/bar)
# Use quarter-beat = 1 beat for pulse
beat = 4  # align to first chord
while beat < song_end_beat_approx:
    t = beat_to_sec_v2(beat)
    beat_in_bar = round(beat) % 4  # 0,1,2,3

    # Kick on beat 0 (bar start) and sometimes beat 2
    if beat_in_bar == 0:
        drum_notes.append({'t': round(t, 4), 'lane': KICK})
    elif beat_in_bar == 2:
        drum_notes.append({'t': round(t, 4), 'lane': ALT_KICK})

    # Snare on beats 1 and 3
    if beat_in_bar == 1 or beat_in_bar == 3:
        drum_notes.append({'t': round(t, 4), 'lane': SNARE})

    # Hihat every beat
    drum_notes.append({'t': round(t + 0.01, 4), 'lane': HIHAT})

    # Off-beat hihat (8th notes)
    half_beat_t = beat_to_sec_v2(beat + 0.5)
    drum_notes.append({'t': round(half_beat_t + 0.01, 4), 'lane': HIHAT})

    beat += 1

# Sort by time
drum_notes.sort(key=lambda x: x['t'])
print(f"Drum notes: {len(drum_notes)}")

# ── Build CHART_DATA ──────────────────────────────────────────────────────────
chart = {
    'bpm': 72.5,
    'drumNotes': drum_notes,
    'chords': filtered_chords
}

chart_json = json.dumps(chart, ensure_ascii=False, separators=(',', ':'))
lyrics_json = json.dumps(lyrics, ensure_ascii=False, indent=None)

print(f"\nChart JSON length: {len(chart_json)}")
print(f"Lyrics JSON lines: {len(lyrics)}")

# ── Patch index.html ──────────────────────────────────────────────────────────
with open(r'D:\drum_keyboard\index.html', encoding='utf-8') as f:
    html = f.read()

# 1. Update title
html = html.replace('<title>大风吹 · 合奏</title>', '<title>不为谁而作的歌 · 合奏</title>')
html = html.replace('<h1>大风吹 · 合奏</h1>', '<h1>不为谁而作的歌 · 合奏</h1>')

# 2. Replace CHART_DATA
html = re.sub(r'const CHART_DATA = \{.*?\};',
              f'const CHART_DATA = {chart_json};',
              html, flags=re.DOTALL)

# 3. Replace LYRICS constant (find const LYRICS = [...]; block)
lyrics_lines = ['const LYRICS = [']
for l in lyrics:
    lyrics_lines.append(f"  {{t:{l['t']:.3f},text:{json.dumps(l['text'], ensure_ascii=False)}}},")
lyrics_lines.append('];')
new_lyrics_block = '\n'.join(lyrics_lines)

html = re.sub(r'const LYRICS = \[.*?\];', new_lyrics_block, html, flags=re.DOTALL)

# 4. Replace drawLyrics with full-width bar version
old_drawLyrics = '''function drawLyrics(now){
  if(!gameRunning||lyricIdx<0) return;
  const lyric=LYRICS[lyricIdx];
  const nextT=lyricIdx<LYRICS.length-1?LYRICS[lyricIdx+1].t:lyric.t+4;
  if(now>nextT+0.3) return;
  const W=canvas.width;
  ctx.font='bold 24px "Segoe UI",Arial';
  ctx.textAlign='center';
  const tw=ctx.measureText(lyric.text).width;
  const pw=tw+40,ph=42,px=W/2-pw/2,py=12;
  ctx.fillStyle='rgba(255,255,255,0.93)';
  rrect(ctx,px,py,pw,ph,8); ctx.fill();
  ctx.strokeStyle='#1a1a2e'; ctx.lineWidth=2.5;
  rrect(ctx,px,py,pw,ph,8); ctx.stroke();
  ctx.fillStyle='#1a1a2e';
  ctx.fillText(lyric.text,W/2,py+ph*0.67);
}'''

new_drawLyrics = '''function drawLyrics(now){
  if(!gameRunning||lyricIdx<0) return;
  const lyric=LYRICS[lyricIdx];
  const nextT=lyricIdx<LYRICS.length-1?LYRICS[lyricIdx+1].t:lyric.t+4;
  if(now>nextT+0.5) return;
  const W=canvas.width, H=canvas.height;
  const LYRH=56, LYRY=0;
  // Full-width bar background
  ctx.fillStyle='#fff';
  ctx.fillRect(0, LYRY, W, LYRH);
  // Bottom border
  ctx.strokeStyle='#1a1a2e'; ctx.lineWidth=3;
  ctx.beginPath(); ctx.moveTo(0,LYRY+LYRH); ctx.lineTo(W,LYRY+LYRH); ctx.stroke();
  // ♪ VOCAL label
  ctx.fillStyle='#ccc'; ctx.font='bold 10px Arial'; ctx.textAlign='left';
  ctx.fillText('♪ VOCAL', 14, LYRY+LYRH*0.67);
  // Current lyric
  ctx.font='bold 34px "Segoe UI",Arial';
  ctx.textAlign='center';
  ctx.fillStyle='#1a1a2e';
  ctx.fillText(lyric.text, W/2, LYRY+LYRH*0.68);
  // Next lyric preview (faded, below bar)
  if(lyricIdx<LYRICS.length-1){
    const next=LYRICS[lyricIdx+1];
    ctx.font='18px "Segoe UI",Arial';
    ctx.fillStyle='rgba(26,26,46,0.25)';
    ctx.fillText(next.text, W/2, LYRY+LYRH+24);
  }
}'''

if old_drawLyrics in html:
    html = html.replace(old_drawLyrics, new_drawLyrics)
    print("✓ drawLyrics replaced")
else:
    print("✗ drawLyrics not found exactly — trying flexible replace")
    html = re.sub(r'function drawLyrics\(now\)\{.*?\}', new_drawLyrics, html, flags=re.DOTALL)

# 5. Replace scheduleSynths(CHART_DATA) call in startGame with Tone.Player for mp3
old_start = '  scheduleSynths(CHART_DATA);\n  document.getElementById(\'overlay\').style.display=\'none\';'
new_start = '''  // Background music via MP3
  if(!window._bgPlayer){
    window._bgPlayer = new Tone.Player('song.mp3').toDestination();
    window._bgPlayer.sync().start(0);
  }
  document.getElementById('overlay').style.display='none';'''

if old_start in html:
    html = html.replace(old_start, new_start)
    print("✓ scheduleSynths replaced with Tone.Player")
else:
    print("✗ scheduleSynths call not found exactly")
    # Try regex
    html = re.sub(r"\s*scheduleSynths\(CHART_DATA\);",
                  "\n  if(!window._bgPlayer){window._bgPlayer=new Tone.Player('song.mp3').toDestination();window._bgPlayer.sync().start(0);}",
                  html)

# 6. Update status text in loadChart (mentions BPM)
html = html.replace(
    "st.textContent=`Ready — ${drumNotes.length} drum · ${chords.length} chords · ${CHART_DATA.bpm} BPM`;",
    "st.textContent=`Ready — ${drumNotes.length} drum · ${chords.length} chords · ${CHART_DATA.bpm} BPM · 不为谁而作的歌`;"
)

# Write
with open(r'D:\drum_keyboard\index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("\n✅ index.html patched successfully!")
print(f"   Title: 不为谁而作的歌 · 合奏")
print(f"   Drum notes: {len(drum_notes)}")
print(f"   Chords: {len(filtered_chords)}")
print(f"   Lyrics: {len(lyrics)} lines")
