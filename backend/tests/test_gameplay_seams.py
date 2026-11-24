from pathlib import Path
import subprocess
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gameplay_seams import BLOCK, FFMPEG, RATE, edit_sound, replace_frames


def test_music_bridge_preserves_source_timing_without_a_silent_join(tmp_path):
    t = np.arange(5*RATE) / RATE
    tone = np.rint(7000*np.sin(2*np.pi*217*t)).astype('<i2')
    pcm = np.column_stack((tone,-tone))
    pcm.tofile(tmp_path/'in')
    edit_sound(tmp_path/'in', [(0,48),(73,120)], tmp_path/'out')
    out = np.fromfile(tmp_path/'out','<i2').reshape(-1,2)
    join = 48*BLOCK
    assert len(out) == 95*BLOCK
    np.testing.assert_array_equal(out[:join-9600], pcm[:join-9600])
    np.testing.assert_array_equal(out[join+43200:], pcm[73*BLOCK+43200:])
    np.testing.assert_array_equal(out[:,0], -out[:,1])
    window = out[join-9600:join+43200,0].astype(float)
    assert np.min(np.sqrt(np.mean(window.reshape(-1,480)**2,axis=1))) > 3800
    assert np.max(np.abs(np.diff(window))) < 500


def test_video_replaces_only_requested_frames_without_holds_or_drift(tmp_path):
    base = np.arange(60,dtype='u1')
    patch = np.arange(211,217,dtype='u1')
    inputs = []
    for values, name in ((base,'base'),(patch,'patch')):
        path = tmp_path/name
        np.repeat(values,64).tofile(path)
        inputs += ['-f','rawvideo','-pix_fmt','gray','-r','24','-s','8x8','-i',str(path)]
    graph = '[0:v]setsar=1[base];'+replace_frames('[base]','[1:v]',[(8,2),(30,4)])+'[v]'
    data = subprocess.check_output([FFMPEG,'-v','error',*inputs,'-filter_complex',graph,
        '-map','[v]','-threads','1','-pix_fmt','gray','-f','rawvideo','-'])
    base[8:10],base[30:34] = patch[:2],patch[2:]
    np.testing.assert_array_equal(np.frombuffer(data,'u1').reshape(-1,64)[:,0],base)
