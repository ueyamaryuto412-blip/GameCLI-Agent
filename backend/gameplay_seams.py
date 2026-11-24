"""Join recorded idle animation and game music on the existing frame/sample clock."""
from pathlib import Path
import subprocess

import cv2
import imageio_ffmpeg
import numpy as np

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
RATE = 48000
FPS = 24
BLOCK = RATE // FPS
WIDTH, HEIGHT = 1920, 1080
SCALE = ("scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,"
         "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1")


def joins(spans):
    at = 0
    for (start, left), (right, end) in zip(spans, spans[1:]):
        at += left - start
        # Leave untouched samples/frames between adjacent transitions.
        room = min((left-start)//2, (end-right)//2, right-left)
        if room > 0:
            yield at, left, right, room


def ramp(size):
    x = np.linspace(0, 1, size, dtype=np.float32)
    return (x*x*(3-2*x))[:, None]


def stretch_grains(source, length):
    """Match overlapping waveform grains in stereo, pinning both ends to the recording."""
    if length == len(source):
        return source.copy()
    hop = min(720, length//6, len(source)//6)
    if hop < 2:
        return cv2.resize(source, (2, length), interpolation=cv2.INTER_LINEAR)
    out = np.zeros((length, 2), np.float32)
    out[:2*hop] = source[:2*hop]
    position = 0
    fade = ramp(hop)
    for pos in range(hop, length - 2*hop + 1, hop):
        expected = round(pos * (len(source)-2*hop) / (length-2*hop))
        lo = max(position, expected-hop//2)
        hi = min(len(source)-2*hop, expected+hop//2)
        if hi < lo:
            lo = hi
        reference = np.ascontiguousarray(out[pos:pos+hop])
        match = cv2.matchTemplate(source[lo:hi+hop], reference, cv2.TM_CCORR_NORMED)
        position = lo + int(match.argmax())
        grain = source[position:position+2*hop]
        out[pos:pos+hop] = reference*(1-fade) + grain[:hop]*fade
        out[pos+hop:pos+2*hop] = grain[hop:]
    tail = pos + hop
    remaining = length-tail
    overlap = min(hop, remaining)
    weight = ramp(overlap)
    out[tail:tail+overlap] = out[tail:tail+overlap]*(1-weight) + source[-remaining:][:overlap]*weight
    out[tail+overlap:] = source[len(source) - (remaining-overlap):]
    return out


def music_bridge(pcm, left_frame, right_frame, room):
    """Keep both tails alive; align similar sound before returning to the live effects clock."""
    left, right = left_frame*BLOCK, right_frame*BLOCK
    half = min(9600, room*BLOCK//4)
    after = min(43200, room*BLOCK)
    search = min(7680, half, (after-half)//3)
    # Match the game's recorded stereo waveform, allowing a small difference in beat phase.
    outgoing = pcm[left-half:left+half].astype(np.float32)
    candidates = pcm[right-half-search:right+half+search].astype(np.float32)
    def reduce(x):
        return np.ascontiguousarray(x[:len(x)//8*8].reshape(-1,8,2).mean(axis=1))
    match = cv2.matchTemplate(reduce(candidates), reduce(outgoing), cv2.TM_CCORR_NORMED)
    lag = int(match.argmax())*8 - search if np.any(outgoing) else 0
    incoming = pcm[right-half+lag:right+half+lag].astype(np.float32)
    weight = ramp(2*half)
    # Normalize for correlation: unrelated passages keep their loudness, identical tones stay at unity.
    den = np.sqrt(np.sum(outgoing.astype('f8')**2) * np.sum(incoming.astype('f8')**2))
    correlation = np.clip(np.sum(outgoing*incoming) / den, 0, 1) if den else 0
    norm = np.sqrt((1-weight)**2 + weight**2 + 2*correlation*weight*(1-weight))
    fade = (outgoing*(1-weight) + incoming*weight)/norm
    tail = stretch_grains(pcm[right+half+lag:right+after].astype(np.float32), after-half)
    return half, np.rint(np.clip(np.concatenate((fade, tail)), -32768, 32767)).astype('<i2')


def decode_sound(source, target):
    subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-y", "-i", str(source), "-vn",
                    "-map", "0:a:0", "-af", "aresample=48000:async=1:first_pts=0", "-ac", "2",
                    "-f", "s16le", str(target)], check=True)


def edit_sound(source, spans, target):
    pcm = np.memmap(source, '<i2', mode='r').reshape(-1, 2)
    with Path(target).open('w+b') as file:
        for a, b in spans:
            chunk = pcm[a*BLOCK:b*BLOCK]
            chunk.tofile(file)
            missing = (b-a)*BLOCK-len(chunk)
            if missing > 0:
                file.write(bytes(missing*4))
        for at, left, right, room in joins(spans):
            if min(len(pcm)-right*BLOCK, len(pcm)-left*BLOCK) < room*BLOCK:
                room = min(room, (len(pcm)-right*BLOCK)//BLOCK)
            if room < 1:
                continue
            before, bridge = music_bridge(pcm,left,right,room)
            file.seek((at*BLOCK-before)*4)
            bridge.tofile(file)


def patches(source, spans, work):
    """Make short moving dissolves using actual footage from both sides of each idle cut."""
    plan = []
    path = Path(work)/"seams.nut"
    frame_bytes = WIDTH*HEIGHT*3//2
    def frames(start, count):
        data = subprocess.check_output([FFMPEG, "-v", "error", "-nostdin", "-threads", "4",
            "-ss", f"{start/FPS:.8f}", "-i", str(source), "-an", "-vf", SCALE,
            "-frames:v", str(count), "-pix_fmt", "yuv420p", "-threads", "1", "-f", "rawvideo", "-"])
        return np.frombuffer(data, 'u1').reshape(count, frame_bytes)
    cmd = [FFMPEG,"-v","error","-y","-f","rawvideo","-pix_fmt","yuv420p","-s","1920x1080",
           "-r",str(FPS),"-i","-","-c:v","ffv1","-threads","4",str(path)]
    if not any(joins(spans)):
        return None, []
    with subprocess.Popen(cmd,stdin=subprocess.PIPE) as encoder:
        try:
            for at,left,right,room in joins(spans):
                h = min(5,room)
                a,b = frames(left-h,2*h),frames(right-h,2*h)
                # On stationary menus a dissolve provides no motion benefit.
                def thumbnail(frame):
                    y = frame[:WIDTH*HEIGHT].reshape(HEIGHT,WIDTH)
                    return cv2.resize(y,(160,90),interpolation=cv2.INTER_AREA).astype('i2')
                outgoing, incoming = thumbnail(a[h-1]), thumbnail(b[h])
                jump = np.abs(incoming-outgoing).mean()
                normal = max(np.abs(thumbnail(a[h])-outgoing).mean(),
                             np.abs(incoming-thumbnail(b[h-1])).mean())
                if jump <= max(.18, normal*1.25):
                    continue
                for first, second, weight in zip(a,b,ramp(2*h+2)[1:-1,0]):
                    encoder.stdin.write(cv2.addWeighted(first,1-float(weight),second,float(weight),0).tobytes())
                plan.append((at-h,2*h))
        finally:
            encoder.stdin.close()
        if encoder.wait():
            raise RuntimeError("Failed to encode moving cut transitions")
    return (path if plan else None), plan


def replace_frames(base, patch, plan):
    """Interleave the replacement frames at their exact original frame numbers."""
    boundary = 0
    shifts = []
    keep = []
    for start, count in plan:
        shifts.append((boundary+count, start-boundary))
        keep.append(f"between(n,{start},{start+count-1})")
        boundary += count
    def lookup(items):
        if len(items)==1:
            return str(items[0][1])
        mid = len(items)//2
        return f"if(lt(N,{items[mid-1][0]}),{lookup(items[:mid])},{lookup(items[mid:])})"
    return (f"{base}settb=1/24,setpts=N,select='not({'+'.join(keep)})'[retained];"
            f"{patch}setsar=1,settb=1/24,setpts='N+{lookup(shifts)}'[replacement];"
            "[retained][replacement]interleave=nb_inputs=2:duration=longest,settb=1/24,setpts=N,fps=24:eof_action=pass")
