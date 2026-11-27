#!/usr/bin/env python3
"""Compile libbluray's actual _bd_read with deterministic clip/event stubs.

Pass the paired patched src/libbluray/bluray.c. Filesystem/VM/graphics are stubbed;
this tests navigation byte/event ordering, not a full native player or disc.
The pre-fix source is accepted so the same test demonstrates the prior failure.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', type=Path)
args = parser.parse_args()
source = args.source.read_text()
method = source[source.index('static int _bd_read('):source.index('static int _bd_read_locked(')]
call = '_bd_read(bd, buf, len, nav)' if 'int stop_at_boundary)' in method else '_bd_read(bd, buf, len)'
preamble = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#define SPN(x) ((x)/192)
#define BD_UNLIKELY(x) (x)
#define BD_DEBUG(...) ((void)0)
#define TS_PID(x) 8191
constexpr int BLURAY_STILL_INFINITE=2,BLURAY_STILL_TIME=1;
constexpr int CONNECT_NON_SEAMLESS=0,HDMV_PID_PCR=4097;
constexpr int BD_EVENT_STILL_TIME=1,BD_EVENT_END_OF_TITLE=2,BD_EVENT_DISCONTINUITY=3;
constexpr int GC_CTRL_INIT_MENU=1,GC_CTRL_PG_UPDATE=2;
constexpr int UO=4,PLAYITEM=5,ANGLE=6,SEEK=7;
struct Clip { unsigned end_pkt=1,title_pkt=0,still_mode=0,still_time=0,connection=1,in_time=0; };
struct BD_STREAM {
  Clip* clip=nullptr;
  uint64_t clip_pos=0;
  unsigned int int_buf_off=0;
  int ig_pid=0,pg_pid=0,seek_flag=0;
};
struct BLURAY {
  BD_STREAM st0,st_textst;
  bool seamless_angle_change=false;
  uint32_t angle_change_pkt=1,angle_change_time=0;
  uint64_t s_pos=0;
  void* title=nullptr;void* graphics_controller=nullptr;
  bool event_queue=true;
  int end_of_playlist=0,opens=0,angles=0;
  uint8_t int_buf[6144];
  std::deque<int> events;
};
Clip first,second;
static Clip* nav_next_clip(void*,Clip* c) {return c==&first?&second:nullptr;}
static void _queue_event(BLURAY* bd,int event,unsigned) {bd->events.push_back(event);}
static int _open_m2ts(BLURAY* bd,BD_STREAM* st) {
  ++bd->opens;st->clip_pos=0;st->int_buf_off=0;
  std::memset(bd->int_buf,'B',sizeof(bd->int_buf));
  bd->events.push_back(UO);bd->events.push_back(PLAYITEM);return 1;
}
static void _change_angle(BLURAY* bd) {++bd->angles;bd->events.push_back(ANGLE);}
static void _clip_seek_time(BLURAY* bd,unsigned) {
  bd->st0.clip_pos=0;bd->st0.int_buf_off=0;
  std::memset(bd->int_buf,'B',sizeof(bd->int_buf));bd->events.push_back(SEEK);
}
static int _read_block(BLURAY*,BD_STREAM*,uint8_t*) {return 1;}
static int gc_decode_ts(void*,int,uint8_t*,int,int) {return 0;}
static void _run_gc(BLURAY*,int,int) {}
static void gc_run(void*,int,int,void*) {}
static void _update_textst_timer(BLURAY*) {}
'''
tests = r'''
BLURAY initial(bool angle=false) {
  first=Clip{};second=Clip{};
  BLURAY bd;bd.st0.clip=&first;std::memset(bd.int_buf,'A',sizeof(bd.int_buf));
  bd.seamless_angle_change=angle;
  if(angle)first.end_pkt=3;
  return bd;
}
void expect(int actual,int wanted,const char* description) {
  if(actual!=wanted){std::fprintf(stderr,"%s: expected %d, got %d\n",description,wanted,actual);std::abort();}
}
int main() {
  uint8_t buf[384];
  {
    auto bd=initial();expect(read_mode(&bd,buf,192,1),192,"old clip bytes");
    expect(read_mode(&bd,buf,192,1),0,"events before new-clip bytes");
    assert(bd.opens==1 && bd.events==std::deque<int>({UO,PLAYITEM}));
    // _read_ext drains these in queue order before calling _bd_read again.
    assert(bd.events.front()==UO);bd.events.pop_front();
    assert(bd.events.front()==PLAYITEM);bd.events.pop_front();
    expect(read_mode(&bd,buf,192,1),192,"new clip after events");
    assert(buf[0]=='B' && bd.opens==1);
  }
  {
    auto bd=initial();expect(read_mode(&bd,buf,192,0),192,"direct old");
    expect(read_mode(&bd,buf,192,0),192,"direct read unchanged");assert(buf[0]=='B');
  }
  {
    auto bd=initial(true);expect(read_mode(&bd,buf,384,1),192,"angle old prefix");
    assert(bd.angles==0 && bd.events.empty() && buf[191]=='A');
    expect(read_mode(&bd,buf,192,1),0,"angle events before bytes");
    assert(bd.angles==1 && bd.events==std::deque<int>({ANGLE,SEEK}));bd.events.clear();
    expect(read_mode(&bd,buf,192,1),192,"angle new bytes");assert(buf[0]=='B' && bd.angles==1);
  }
  {
    auto bd=initial(true);expect(read_mode(&bd,buf,384,0),384,"direct angle unchanged");
    assert(buf[191]=='A' && buf[192]=='B');
  }
  {
    auto bd=initial(true);first.end_pkt=1;
    expect(read_mode(&bd,buf,192,1),192,"angle end old");
    expect(read_mode(&bd,buf,192,1),0,"angle next clip events");
    assert(bd.opens==1 && bd.st0.clip==&second);bd.events.clear();
    expect(read_mode(&bd,buf,192,1),192,"angle next clip bytes");assert(buf[0]=='B');
  }
  std::puts("PASS: normal/angle event-before-byte boundaries, old-prefix splitting and direct reads");
}
'''
with tempfile.TemporaryDirectory(prefix='bluray-boundary-') as tmp:
    path = Path(tmp) / 'test.cpp'
    wrapper = f'int read_mode(BLURAY* bd,unsigned char* buf,int len,int nav) {{(void)nav; return {call};}}\n'
    path.write_text(preamble + method + wrapper + tests)
    subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-Wno-sign-compare',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(path), '-o', str(path.with_suffix(''))], check=True)
    subprocess.run([str(path.with_suffix(''))], check=True)
