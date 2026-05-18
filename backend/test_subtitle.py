import subprocess, os, sys
sys.stdout.reconfigure(encoding='utf-8')

os.makedirs('outputs', exist_ok=True)
TEST_VIDEO = 'outputs/test40.mp4'
TEST_SRT   = 'outputs/test40.srt'
TEST_SUB   = 'outputs/test40_sub.mp4'
TEST_NOSUB = 'outputs/test40_nosub.mp4'

# 1. Create 40s test video
subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error',
    '-f','lavfi','-i','color=c=blue:size=1920x1080:duration=40:rate=25',
    '-f','lavfi','-i','sine=frequency=440:duration=40',
    '-shortest','-c:v','libx264','-c:a','aac', TEST_VIDEO], capture_output=True)
print('Test video:', os.path.getsize(TEST_VIDEO), 'bytes')

# 2. Create SRT
with open(TEST_SRT,'w',encoding='utf-8') as f:
    f.write('1\n00:00:00,000 --> 00:00:05,000\nVoce nunca vai acreditar!\n\n')
    f.write('2\n00:00:05,000 --> 00:00:10,000\nA regra numero um\n\n')
    f.write('3\n00:00:10,000 --> 00:00:40,000\nFim do video!\n\n')

# 3. Windows path escaping
srt_abs = os.path.abspath(TEST_SRT)
# Replace backslash with forward slash, then escape colon
srt_ff = srt_abs.replace('\\', '/').replace(':', '\\:')
print('SRT abs:', srt_abs)
print('SRT ffmpeg:', srt_ff)

# 4. Test WITH subtitles
vf_sub = (
    "crop=h*9/16:h:x=(w-h*9/16)/2:y=0,"
    "scale=1080:1920,"
    "subtitles='" + srt_ff + "':force_style="
    "'FontSize=20,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=2'"
)
r = subprocess.run(
    ['ffmpeg','-y','-hide_banner','-loglevel','warning',
     '-ss','0','-t','40','-i',TEST_VIDEO,
     '-vf', vf_sub,
     '-c:v','libx264','-preset','ultrafast','-c:a','aac', TEST_SUB],
    capture_output=True, text=True, encoding='utf-8', errors='replace'
)
print('Subtitle burn return code:', r.returncode)
if r.stderr: print('Stderr:', r.stderr[-400:])

if os.path.exists(TEST_SUB):
    r2 = subprocess.run(['ffprobe','-v','quiet','-show_entries','format=duration',
        '-of','csv=p=0', TEST_SUB], capture_output=True, text=True)
    print('WITH subtitles duration:', r2.stdout.strip(), 's')
else:
    print('SUBTITLE BURN FAILED - file not created')

# 5. Test WITHOUT subtitles (control)
vf_nosub = "crop=h*9/16:h:x=(w-h*9/16)/2:y=0,scale=1080:1920"
r3 = subprocess.run(
    ['ffmpeg','-y','-hide_banner','-loglevel','error',
     '-ss','0','-t','40','-i',TEST_VIDEO,
     '-vf', vf_nosub,
     '-c:v','libx264','-preset','ultrafast','-c:a','aac', TEST_NOSUB],
    capture_output=True, text=True, encoding='utf-8', errors='replace'
)
r4 = subprocess.run(['ffprobe','-v','quiet','-show_entries','format=duration',
    '-of','csv=p=0', TEST_NOSUB], capture_output=True, text=True)
print('WITHOUT subtitles duration:', r4.stdout.strip(), 's')
print()
print('CONCLUSION:')
if os.path.exists(TEST_SUB):
    print('Subtitle filter WORKS on this system')
else:
    print('Subtitle filter BROKEN - use drawtext fallback')
