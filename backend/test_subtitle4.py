"""Find the correct crop formula + subtitle burning approach."""
import subprocess, os, sys
sys.stdout.reconfigure(encoding='utf-8')

TV  = 'outputs/test40.mp4'
SRT = 'outputs/test40.srt'
srt_dir  = os.path.abspath(os.path.dirname(SRT))
srt_name = os.path.basename(SRT)
tv_abs   = os.path.abspath(TV)

def dur(f):
    if not os.path.exists(f) or os.path.getsize(f) < 500: return 'FAIL'
    r = subprocess.run(['ffprobe','-v','quiet','-show_entries','format=duration','-of','csv=p=0',f],
        capture_output=True, text=True)
    return r.stdout.strip()+'s'

def ffrun(tag, out_path, vf, cwd=None):
    cmd = ['ffmpeg','-y','-hide_banner','-loglevel','warning',
           '-ss','0','-t','40','-i', tv_abs,
           '-vf', vf,
           '-c:v','libx264','-preset','ultrafast','-c:a','aac', out_path]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=cwd)
    d = dur(out_path)
    ok = 'FAIL' not in d
    print(f'[{"OK" if ok else "FAIL"}] {tag}: {d}')
    if not ok and r.stderr: print('  >', r.stderr[-150:].replace('\n',' '))
    return ok

# FIX 1: Use trunc to get even dimensions
# trunc(h*9/16/2)*2 ensures even width
crop_even = "crop=trunc(h*9/16/2)*2:h:x=(w-trunc(h*9/16/2)*2)/2:y=0,scale=1080:1920:flags=lanczos"
ffrun('FIX1 crop even dims', 'outputs/fix1_crop.mp4', crop_even)

# FIX 2: crop even + subtitles two-pass (crop first, then subtitle)
tmp = 'outputs/fix2_tmp.mp4'
r_crop = subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error',
    '-ss','0','-t','40','-i', tv_abs,
    '-vf', crop_even,
    '-c:v','libx264','-preset','ultrafast','-c:a','aac', tmp], capture_output=True)
print('Crop pass:', 'OK' if os.path.exists(tmp) and os.path.getsize(tmp)>500 else 'FAIL')

# Now add subtitles with relative cwd
cmd2 = ['ffmpeg','-y','-hide_banner','-loglevel','warning',
    '-i', os.path.abspath(tmp),
    '-vf', f"subtitles='{srt_name}':force_style='FontSize=20,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=2'",
    '-c:v','libx264','-preset','ultrafast','-c:a','aac', 'fix2_final.mp4']
r2 = subprocess.run(cmd2, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=srt_dir)
d2 = dur(os.path.join(srt_dir,'fix2_final.mp4'))
print(f'[{"OK" if "FAIL" not in d2 else "FAIL"}] FIX2 two-pass (crop+subs): {d2}')
if 'FAIL' in d2 and r2.stderr: print('  >', r2.stderr[-200:].replace('\n',' '))

# FIX 3: crop even + drawtext (with font)
import glob
fonts = glob.glob(r'C:\Windows\Fonts\arial*.ttf')
if fonts:
    font_path = fonts[0].replace('\\','/').replace(':','\\:')
    crop_dt = (crop_even + "," +
        f"drawtext=fontfile='{font_path}':text='Legenda teste 123':fontsize=44:fontcolor=white"
        f":borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-100:enable='between(t,0,40)'")
    ffrun('FIX3 crop+drawtext+arial', 'outputs/fix3.mp4', crop_dt)
else:
    print('[SKIP] No arial font found')

print()
print('FINAL: best approach for this system:')
for tag,f in [('FIX1 crop even','outputs/fix1_crop.mp4'),
              ('FIX2 two-pass',os.path.join(srt_dir,'fix2_final.mp4')),
              ('FIX3 drawtext','outputs/fix3.mp4')]:
    d = dur(f)
    if 'FAIL' not in d: print(f'  WORKS: {tag}')
