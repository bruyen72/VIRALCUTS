"""Diagnose subtitle filter + find working approach."""
import subprocess, os, sys
sys.stdout.reconfigure(encoding='utf-8')

TV  = 'outputs/test40.mp4'
SRT = 'outputs/test40.srt'

def dur(f):
    if not os.path.exists(f) or os.path.getsize(f) < 500: return 'FAIL/EMPTY'
    r = subprocess.run(['ffprobe','-v','quiet','-show_entries','format=duration','-of','csv=p=0',f],
        capture_output=True, text=True)
    return r.stdout.strip() + 's'

def ffrun(tag, out, vf, extra=None):
    e = extra or []
    cmd = ['ffmpeg','-y','-hide_banner','-loglevel','warning','-ss','0','-t','40',
           '-i', os.path.abspath(TV), '-vf', vf, '-c:v','libx264','-preset','ultrafast','-c:a','aac'] + e + [out]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    d = dur(out)
    print(f'[{"OK" if "FAIL" not in d else "FAIL"}] {tag}: {d}')
    if 'FAIL' in d and r.stderr: print('  >', r.stderr[-250:].replace('\n',' '))
    return 'FAIL' not in d

srt_abs = os.path.abspath(SRT)
srt_name = os.path.basename(SRT)
srt_dir  = os.path.dirname(srt_abs)
srt_fwd  = srt_abs.replace('\\','/')

# T1: Just crop+scale (no subtitles) - verify base works
ffrun('T1 crop+scale only',  'outputs/t1.mp4',  'crop=h*9/16:h:x=(w-h*9/16)/2:y=0,scale=1080:1920')

# T2: Just subtitles no crop (relative path, cwd=srt_dir)
cmd2 = ['ffmpeg','-y','-hide_banner','-loglevel','warning','-ss','0','-t','40',
        '-i', os.path.abspath(TV),
        '-vf', f"subtitles='{srt_name}'",
        '-c:v','libx264','-preset','ultrafast','-c:a','aac','t2.mp4']
r2 = subprocess.run(cmd2, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=srt_dir)
d2 = dur(os.path.join(srt_dir,'t2.mp4'))
print(f'[{"OK" if "FAIL" not in d2 else "FAIL"}] T2 subtitles only (relative cwd): {d2}')
if 'FAIL' in d2 and r2.stderr: print(' >', r2.stderr[-200:].replace('\n',' '))

# T3: crop first, THEN subtitles with original_size set
ffrun('T3 crop then subtitles+original_size',  'outputs/t3.mp4',
    f"crop=h*9/16:h:x=(w-h*9/16)/2:y=0,scale=1080:1920,subtitles='{srt_fwd}':original_size=1080x1920")

# T4: subtitles FIRST (before crop)
ffrun('T4 subtitles FIRST then crop', 'outputs/t4.mp4',
    f"subtitles='{srt_fwd}',crop=h*9/16:h:x=(w-h*9/16)/2:y=0,scale=1080:1920")

# T5: Two-pass: crop first, then subtitles in second pass
tmp5 = 'outputs/t5_tmp.mp4'
out5 = 'outputs/t5.mp4'
subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-ss','0','-t','40',
    '-i', os.path.abspath(TV),'-vf','crop=h*9/16:h:x=(w-h*9/16)/2:y=0,scale=1080:1920',
    '-c:v','libx264','-preset','ultrafast','-c:a','aac', tmp5], capture_output=True)
cmd5 = ['ffmpeg','-y','-hide_banner','-loglevel','warning',
    '-i', os.path.abspath(tmp5),
    '-vf', f"subtitles='{srt_name}'",
    '-c:v','libx264','-preset','ultrafast','-c:a','aac', 't5.mp4']
r5 = subprocess.run(cmd5, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=srt_dir)
d5 = dur(os.path.join(srt_dir,'t5.mp4'))
print(f'[{"OK" if "FAIL" not in d5 else "FAIL"}] T5 two-pass (crop then subs): {d5}')
if 'FAIL' in d5 and r5.stderr: print(' >', r5.stderr[-200:].replace('\n',' '))

# T6: drawtext with Windows font path
font_path = r'C:\Windows\Fonts\arial.ttf'
if os.path.exists(font_path):
    font_ff = font_path.replace('\\','/').replace(':','\\:')
    subs_dt = [
        f"drawtext=fontfile='{font_ff}':text='Voce nunca vai acreditar':fontsize=48:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-120:enable='between(t,0,8)'",
        f"drawtext=fontfile='{font_ff}':text='A regra numero um':fontsize=48:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-120:enable='between(t,8,40)'",
    ]
    ffrun('T6 drawtext+arial font', 'outputs/t6.mp4',
        'crop=h*9/16:h:x=(w-h*9/16)/2:y=0,scale=1080:1920,' + ','.join(subs_dt))
else:
    print('[SKIP] T6: arial.ttf not found at', font_path)

print('\n=== SUMMARY ===')
for t,f in [('T1 crop only','outputs/t1.mp4'),('T2 subs only',os.path.join(srt_dir,'t2.mp4')),
             ('T3 crop+subs','outputs/t3.mp4'),('T4 subs+crop','outputs/t4.mp4'),
             ('T5 two-pass',os.path.join(srt_dir,'t5.mp4')),('T6 drawtext','outputs/t6.mp4')]:
    print(f'  {t}: {dur(f)}')
