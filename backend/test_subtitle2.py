"""Test multiple subtitle burning approaches on Windows."""
import subprocess, os, sys, shutil
sys.stdout.reconfigure(encoding='utf-8')

os.makedirs('outputs', exist_ok=True)
TV  = 'outputs/test40.mp4'
SRT = 'outputs/test40.srt'

# Create test video if needed
if not os.path.exists(TV):
    subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error',
        '-f','lavfi','-i','color=c=blue:size=1920x1080:duration=40:rate=25',
        '-f','lavfi','-i','sine=frequency=440:duration=40',
        '-shortest','-c:v','libx264','-c:a','aac', TV], capture_output=True)

# Create SRT
with open(SRT,'w',encoding='utf-8') as f:
    f.write('1\n00:00:00,000 --> 00:00:08,000\nVoce nunca vai acreditar!\n\n')
    f.write('2\n00:00:08,000 --> 00:00:16,000\nA regra numero um\n\n')
    f.write('3\n00:00:16,000 --> 00:00:40,000\nQue ninguem te ensina!\n\n')

srt_abs = os.path.abspath(SRT)
tv_abs  = os.path.abspath(TV)

def test_ffmpeg(name, out, extra_args):
    """Run ffmpeg and return (success, duration)."""
    cmd = ['ffmpeg','-y','-hide_banner','-loglevel','warning',
           '-ss','0','-t','40','-i', tv_abs] + extra_args + [out]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        r2 = subprocess.run(['ffprobe','-v','quiet','-show_entries','format=duration',
            '-of','csv=p=0',out], capture_output=True, text=True)
        dur = r2.stdout.strip()
        print(f'[OK] {name}: {dur}s')
        return True, dur
    else:
        print(f'[FAIL] {name}: rc={r.returncode}')
        if r.stderr: print('  ERR:', r.stderr[-200:])
        return False, None

BASE_VF = 'crop=h*9/16:h:x=(w-h*9/16)/2:y=0,scale=1080:1920'

# Approach 1: Run ffmpeg from SRT directory with relative path (most reliable on Windows)
srt_dir  = os.path.dirname(srt_abs)
srt_name = os.path.basename(SRT)  # just 'test40.srt'
out1 = os.path.join(srt_dir, 'test_approach1.mp4')
vf1  = BASE_VF + f",subtitles='{srt_name}':force_style='FontSize=20,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=2'"
cmd1 = ['ffmpeg','-y','-hide_banner','-loglevel','warning',
        '-ss','0','-t','40','-i', tv_abs,
        '-vf', vf1, '-c:v','libx264','-preset','ultrafast','-c:a','aac', out1]
r1 = subprocess.run(cmd1, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=srt_dir)
if os.path.exists(out1) and os.path.getsize(out1) > 1000:
    r2 = subprocess.run(['ffprobe','-v','quiet','-show_entries','format=duration','-of','csv=p=0',out1], capture_output=True, text=True)
    print(f'[OK] Approach 1 (relative path, cwd=srt_dir): {r2.stdout.strip()}s')
else:
    print(f'[FAIL] Approach 1 (relative path): rc={r1.returncode}')
    if r1.stderr: print('  ERR:', r1.stderr[-200:])

# Approach 2: Forward slashes only (no escape)
out2 = 'outputs/test_approach2.mp4'
srt_fwd = srt_abs.replace('\\', '/')
vf2 = f"subtitles='{srt_fwd}':force_style='FontSize=20,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=2'"
test_ffmpeg('Approach 2 (fwd slashes)', out2,
    ['-vf', BASE_VF + ',' + vf2, '-c:v','libx264','-preset','ultrafast','-c:a','aac'])

# Approach 3: drawtext for each segment (no file path needed)
out3 = 'outputs/test_approach3.mp4'
# Build a chain of drawtext filters
drawtext_filters = []
subs = [
    (0, 8, 'Voce nunca vai acreditar!'),
    (8, 16, 'A regra numero um'),
    (16, 40, 'Que ninguem te ensina!'),
]
for s_start, s_end, text in subs:
    dt = (f"drawtext=text='{text}'"
          f":fontsize=48:fontcolor=white:borderw=3:bordercolor=black"
          f":x=(w-text_w)/2:y=h-100"
          f":enable='between(t,{s_start},{s_end})'")
    drawtext_filters.append(dt)

vf3 = BASE_VF + ',' + ','.join(drawtext_filters)
test_ffmpeg('Approach 3 (drawtext)', out3,
    ['-vf', vf3, '-c:v','libx264','-preset','ultrafast','-c:a','aac'])

# Approach 4: subtitles with charenc and full Windows path
out4 = 'outputs/test_approach4.mp4'
# Try with different escaping: double backslash
srt_double = srt_abs.replace('\\', '\\\\')
vf4 = f"subtitles='{srt_double}':charenc=UTF-8:force_style='FontSize=20,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=2'"
test_ffmpeg('Approach 4 (double backslash)', out4,
    ['-vf', BASE_VF + ',' + vf4, '-c:v','libx264','-preset','ultrafast','-c:a','aac'])

print('\nBEST APPROACH TO USE:')
for i, f in [(1,out1),(3,'outputs/test_approach3.mp4')]:
    path = out1 if i==1 else f
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        print(f'  Approach {i} works!')
